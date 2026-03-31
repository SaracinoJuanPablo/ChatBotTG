# src/agent/knowledge.py
# Gestión de la knowledge base — lectura Y escritura real en disco.
#
# DIFERENCIA CLAVE con la versión bugueada:
#   Antes: las operaciones modificaban self._cache (un dict en RAM) y nunca
#          llamaban a open(..., 'w'). El archivo en disco quedaba intacto.
#   Ahora: cada operación de escritura va primero al disco, luego actualiza
#          el cache en RAM. Si el disco falla, el cache no se toca.
#
# Basado en agent/tools.py del repo whatsapp-agentkit, extendido con
# operaciones de escritura, backups automáticos y hot-reload.

import logging
import shutil
from datetime import datetime
from pathlib import Path

from src.config import config

logger = logging.getLogger("agentkit")


class KnowledgeBase:
    """
    Maneja los archivos de knowledge base en data/knowledge/.

    Responsabilidades:
    - Leer archivos del disco y cachearlos en RAM (para inyectar al LLM)
    - Escribir cambios al disco PRIMERO, luego actualizar el cache
    - Hacer backup automático antes de cualquier escritura
    - Proveer hot-reload: cuando el LLM pida el contexto, siempre
      devuelve la versión más fresca del disco
    """

    def __init__(self):
        self._knowledge_dir: Path = config.knowledge_dir
        self._backups_dir: Path = config.backups_dir
        # Cache en RAM: { "clientes.txt": "Juan\nSofia\n..." }
        # Se recarga desde disco antes de cada lectura importante.
        self._cache: dict[str, str] = {}
        self._cargar_todo()

    # ─────────────────────────────────────────────
    # LECTURA
    # ─────────────────────────────────────────────

    def _cargar_todo(self):
        """
        Lee todos los archivos .txt y .md de knowledge_dir al cache.
        Se llama al iniciar y después de cada escritura.
        """
        self._cache.clear()
        if not self._knowledge_dir.exists():
            logger.warning(f"knowledge_dir no existe: {self._knowledge_dir}")
            return

        for ruta in self._knowledge_dir.iterdir():
            if ruta.is_file() and ruta.suffix in config.allowed_extensions:
                try:
                    self._cache[ruta.name] = ruta.read_text(encoding="utf-8")
                    logger.debug(f"Cargado: {ruta.name}")
                except Exception as e:
                    logger.error(f"Error leyendo {ruta.name}: {e}")

        logger.info(f"Knowledge base cargada — {len(self._cache)} archivo(s)")

    def recargar(self):
        """
        Hot-reload manual. Lo llama brain.py antes de armar el system prompt,
        garantizando que el LLM siempre vea la versión más fresca del disco.
        """
        self._cargar_todo()

    def obtener_contexto_completo(self) -> str:
        """
        Devuelve todo el contenido de la knowledge base concatenado,
        listo para inyectar en el system prompt del LLM.
        Recarga desde disco antes de devolver (hot-reload automático).
        """
        self.recargar()

        if not self._cache:
            return "No hay información de conocimiento disponible."

        secciones = []
        for nombre, contenido in sorted(self._cache.items()):
            secciones.append(f"=== {nombre} ===\n{contenido.strip()}")

        return "\n\n".join(secciones)

    def listar_archivos(self) -> list[str]:
        """Devuelve los nombres de los archivos disponibles (desde disco, no cache)."""
        if not self._knowledge_dir.exists():
            return []
        return sorted(
            f.name
            for f in self._knowledge_dir.iterdir()
            if f.is_file() and f.suffix in config.allowed_extensions
        )

    def ver_archivo(self, nombre: str) -> str | None:
        """
        Devuelve el contenido completo de un archivo.
        Lee directamente del disco (no del cache) para mostrar siempre lo real.
        Devuelve None si el archivo no existe.
        """
        ruta = self._resolver_ruta(nombre)
        if ruta is None or not ruta.exists():
            return None
        return ruta.read_text(encoding="utf-8")

    # ─────────────────────────────────────────────
    # ESCRITURA — todas las operaciones van al disco PRIMERO
    # ─────────────────────────────────────────────

    def agregar_linea(self, nombre: str, texto: str) -> tuple[bool, str]:
        """
        Agrega una línea al final del archivo.
        Crea el archivo si no existe.

        Returns:
            (True, mensaje_ok) o (False, mensaje_error)
        """
        ruta = self._resolver_ruta(nombre)
        if ruta is None:
            return False, f"Nombre de archivo inválido: '{nombre}'"

        # Validar tamaño antes de escribir
        texto_limpio = texto.strip()
        if not texto_limpio:
            return False, "El texto no puede estar vacío."

        try:
            # Hacer backup si el archivo ya existe
            if ruta.exists():
                self._hacer_backup(ruta)
                contenido_actual = ruta.read_text(encoding="utf-8")
                # Asegurarse de que haya salto de línea antes de agregar
                if contenido_actual and not contenido_actual.endswith("\n"):
                    contenido_actual += "\n"
                nuevo_contenido = contenido_actual + texto_limpio + "\n"
            else:
                nuevo_contenido = texto_limpio + "\n"

            # Validar tamaño
            if len(nuevo_contenido.encode("utf-8")) > config.max_file_size_bytes:
                return False, f"El archivo superaría el límite de {config.max_file_size_bytes // 1024} KB."

            # ESCRIBIR EN DISCO ← esto es lo que faltaba en la versión bugueada
            ruta.write_text(nuevo_contenido, encoding="utf-8")

            # Actualizar cache DESPUÉS de escribir en disco exitosamente
            self._cache[ruta.name] = nuevo_contenido

            logger.info(f"Agregado a {nombre}: '{texto_limpio}'")
            return True, f"Línea agregada a `{nombre}`."

        except Exception as e:
            logger.error(f"Error al agregar en {nombre}: {e}")
            return False, f"Error al escribir el archivo: {e}"

    def eliminar_lineas(self, nombre: str, texto: str) -> tuple[bool, str]:
        """
        Elimina todas las líneas que contengan el texto indicado (case-insensitive).
        Hace backup antes de modificar.

        Returns:
            (True, mensaje_ok) o (False, mensaje_error)
        """
        ruta = self._resolver_ruta(nombre)
        if ruta is None:
            return False, f"Nombre de archivo inválido: '{nombre}'"

        if not ruta.exists():
            return False, f"El archivo `{nombre}` no existe."

        texto_buscar = texto.strip().lower()
        if not texto_buscar:
            return False, "El texto a eliminar no puede estar vacío."

        try:
            contenido_actual = ruta.read_text(encoding="utf-8")
            lineas = contenido_actual.splitlines(keepends=True)

            lineas_a_mantener = [
                l for l in lineas
                if texto_buscar not in l.lower()
            ]
            eliminadas = len(lineas) - len(lineas_a_mantener)

            if eliminadas == 0:
                return False, f"No se encontró '{texto}' en `{nombre}`."

            # Backup antes de modificar
            self._hacer_backup(ruta)

            nuevo_contenido = "".join(lineas_a_mantener)

            # ESCRIBIR EN DISCO ← la clave del fix
            ruta.write_text(nuevo_contenido, encoding="utf-8")

            # Actualizar cache después del éxito en disco
            self._cache[ruta.name] = nuevo_contenido

            logger.info(f"Eliminadas {eliminadas} línea(s) con '{texto}' de {nombre}")
            return True, (
                f"Se eliminaron {eliminadas} línea(s) que contenían `{texto}` de `{nombre}`."
            )

        except Exception as e:
            logger.error(f"Error al eliminar en {nombre}: {e}")
            return False, f"Error al modificar el archivo: {e}"

    def reemplazar_contenido(self, nombre: str, nuevo_contenido: str) -> tuple[bool, str]:
        """
        Reemplaza el contenido completo de un archivo.
        Para el comando /editar — el admin manda el contenido nuevo completo.

        Returns:
            (True, mensaje_ok) o (False, mensaje_error)
        """
        ruta = self._resolver_ruta(nombre)
        if ruta is None:
            return False, f"Nombre de archivo inválido: '{nombre}'"

        contenido_limpio = nuevo_contenido.strip() + "\n"

        if len(contenido_limpio.encode("utf-8")) > config.max_file_size_bytes:
            return False, f"El contenido supera el límite de {config.max_file_size_bytes // 1024} KB."

        try:
            # Backup si el archivo ya existe
            if ruta.exists():
                self._hacer_backup(ruta)

            # ESCRIBIR EN DISCO
            ruta.write_text(contenido_limpio, encoding="utf-8")

            # Actualizar cache
            self._cache[ruta.name] = contenido_limpio

            logger.info(f"Contenido completo reemplazado en {nombre}")
            return True, f"El archivo `{nombre}` fue actualizado completamente."

        except Exception as e:
            logger.error(f"Error al reemplazar {nombre}: {e}")
            return False, f"Error al escribir el archivo: {e}"

    # ─────────────────────────────────────────────
    # UTILIDADES INTERNAS
    # ─────────────────────────────────────────────

    def _resolver_ruta(self, nombre: str) -> Path | None:
        """
        Convierte un nombre de archivo a un Path absoluto dentro de knowledge_dir.
        Bloquea path traversal (ej: '../../etc/passwd', '../config.py').

        Returns:
            Path absoluto si es válido, None si es un intento de traversal.
        """
        # Bloquear caracteres peligrosos
        caracteres_prohibidos = {"..", "/", "\\", "\x00"}
        if any(c in nombre for c in caracteres_prohibidos):
            logger.warning(f"Intento de path traversal bloqueado: '{nombre}'")
            return None

        # Asegurar extensión permitida
        ruta = self._knowledge_dir / nombre
        if ruta.suffix not in config.allowed_extensions:
            return None

        # Verificar que la ruta resuelta siga dentro de knowledge_dir
        try:
            ruta.resolve().relative_to(self._knowledge_dir.resolve())
        except ValueError:
            logger.warning(f"Path fuera de knowledge_dir bloqueado: '{nombre}'")
            return None

        return ruta

    def _hacer_backup(self, ruta: Path):
        """
        Copia el archivo actual a backups/ con timestamp antes de modificarlo.
        ej: clientes.txt → backups/clientes_20250327_143022.bak
        """
        if not ruta.exists():
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nombre_backup = f"{ruta.stem}_{timestamp}.bak"
        destino = self._backups_dir / nombre_backup
        try:
            shutil.copy2(ruta, destino)
            logger.debug(f"Backup creado: {nombre_backup}")
        except Exception as e:
            # El backup falla silenciosamente — no bloquea la operación principal.
            logger.warning(f"No se pudo crear backup de {ruta.name}: {e}")


# Instancia global — singleton compartido por handlers.py y brain.py
# Uso: from src.agent.knowledge import knowledge_base
knowledge_base = KnowledgeBase()