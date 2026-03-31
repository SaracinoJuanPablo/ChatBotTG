# src/agent/memory.py
# Gestión del historial conversacional por chat_id.
#
# DIFERENCIA con la versión bugueada:
#   Antes: json.dump() directo al archivo final. Si el proceso muere
#          a mitad de escritura, el JSON queda truncado/corrupto y el
#          bot no arranca en el próximo reinicio.
#   Ahora: escritura atómica — escribimos a un .tmp primero, luego
#          os.replace() (operación atómica del SO). Si algo falla,
#          el archivo original queda intacto.
#
# Diferencia con el repo whatsapp-agentkit:
#   El original usa SQLite + SQLAlchemy async (robusto para producción
#   con múltiples usuarios simultáneos). Para un bot de Telegram personal
#   o de equipo pequeño, JSON es suficiente y elimina dependencias.
#   Si en el futuro necesitás escalar, el reemplazo por SQLite es directo
#   porque la interfaz pública de esta clase no cambia.

import json
import logging
import os
import tempfile
from collections import defaultdict
from pathlib import Path

from src.config import config

logger = logging.getLogger("agentkit")


class ConversationMemory:
    """
    Historial de conversaciones persistido en JSON.

    Estructura del archivo conversations.json:
    {
        "123456789": [                        ← chat_id como string
            {"role": "user",      "content": "Hola"},
            {"role": "assistant", "content": "Hola! ¿En qué te ayudo?"}
        ],
        "987654321": [...]
    }

    La ventana deslizante (max_history) garantiza que el archivo no
    crezca indefinidamente — solo se guardan los últimos N mensajes
    por conversación.
    """

    def __init__(self):
        self._file: Path = config.conversations_file
        self._max_history: int = config.max_history
        # defaultdict: si se pide un chat_id nuevo, devuelve lista vacía
        self._data: defaultdict[str, list[dict]] = defaultdict(list)
        self._cargar()

    # ─────────────────────────────────────────────
    # LECTURA
    # ─────────────────────────────────────────────

    def _cargar(self):
        """
        Carga el historial desde disco al iniciar.
        Si el archivo no existe o está corrupto, arranca con historial vacío
        (nunca crashea al iniciar por un JSON malo).
        """
        if not self._file.exists():
            logger.info("conversations.json no existe — arrancando con historial vacío")
            return

        try:
            raw = self._file.read_text(encoding="utf-8")
            data = json.loads(raw)
            # Validar que sea un dict (no una lista ni un string corrupto)
            if not isinstance(data, dict):
                raise ValueError("El JSON no es un objeto, posiblemente corrupto.")
            self._data = defaultdict(list, data)
            logger.info(f"Historial cargado — {len(self._data)} conversación(es)")
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"conversations.json corrupto: {e}")
            logger.warning("Arrancando con historial vacío. El archivo corrupto se sobreescribirá.")
            self._data = defaultdict(list)

    def obtener_historial(self, chat_id: int | str) -> list[dict]:
        """
        Devuelve el historial de una conversación, listo para enviarlo al LLM.

        Args:
            chat_id: ID del chat de Telegram (int o string, se normaliza internamente)

        Returns:
            Lista de dicts [{"role": "user"|"assistant", "content": "..."}]
            Lista vacía si no hay historial para ese chat.
        """
        key = str(chat_id)
        return list(self._data[key])  # copia defensiva — el llamador no modifica el cache

    # ─────────────────────────────────────────────
    # ESCRITURA
    # ─────────────────────────────────────────────

    def guardar_turno(
        self,
        chat_id: int | str,
        mensaje_usuario: str,
        respuesta_bot: str
    ):
        """
        Guarda un turno completo (mensaje del usuario + respuesta del bot)
        y persiste al disco de forma atómica.

        Guarda los dos mensajes juntos para garantizar consistencia:
        nunca queda un mensaje de usuario sin su respuesta correspondiente
        en el historial (lo que confundiría al LLM en el próximo turno).

        Args:
            chat_id:          ID del chat de Telegram
            mensaje_usuario:  Texto que mandó el usuario
            respuesta_bot:    Texto que respondió el bot
        """
        key = str(chat_id)

        self._data[key].append({"role": "user",      "content": mensaje_usuario})
        self._data[key].append({"role": "assistant", "content": respuesta_bot})

        # Aplicar ventana deslizante — mantener solo los últimos N mensajes
        # Se corta en múltiplos de 2 para no partir un turno a la mitad
        if len(self._data[key]) > self._max_history:
            exceso = len(self._data[key]) - self._max_history
            # Redondear al par más cercano para no dejar un "user" sin "assistant"
            exceso = exceso + (exceso % 2)
            self._data[key] = self._data[key][exceso:]

        self._persistir()

    def limpiar_historial(self, chat_id: int | str):
        """
        Borra el historial de una conversación específica.
        Útil para el comando /reset o cuando el usuario quiere empezar de cero.
        """
        key = str(chat_id)
        if key in self._data:
            del self._data[key]
            self._persistir()
            logger.info(f"Historial borrado para chat_id={chat_id}")

    def limpiar_todo(self):
        """
        Borra TODOS los historiales. Solo para admin/debugging.
        """
        self._data.clear()
        self._persistir()
        logger.warning("Todos los historiales borrados")

    # ─────────────────────────────────────────────
    # PERSISTENCIA ATÓMICA
    # ─────────────────────────────────────────────

    def _persistir(self):
        """
        Escribe el historial al disco de forma atómica.

        Flujo:
        1. Serializar el dict a JSON en memoria
        2. Escribir a un archivo temporal en el MISMO directorio
           (importante: mismo filesystem para que rename sea atómico)
        3. os.replace(tmp → destino) — operación atómica del SO.
           Si el proceso muere entre el paso 2 y 3, el archivo
           original queda intacto. Si muere durante el paso 2,
           solo se pierde el .tmp (que se limpia solo).

        Por qué no json.dump() directo:
        - Si el proceso muere a mitad del dump, el archivo queda
          truncado con JSON inválido.
        - En el próximo reinicio, _cargar() falla y el bot arranca
          sin historial (o directamente crashea si no hay manejo de error).
        """
        try:
            # Serializar primero (si falla aquí, el archivo original no se toca)
            contenido = json.dumps(
                dict(self._data),
                ensure_ascii=False,
                indent=2
            )

            # Escribir al temporal en el mismo directorio que el destino
            # (mismo filesystem = rename atómico garantizado)
            directorio = self._file.parent
            directorio.mkdir(parents=True, exist_ok=True)

            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=directorio,
                suffix=".tmp",
                delete=False
            ) as tmp:
                tmp.write(contenido)
                tmp_path = tmp.name

            # Rename atómico — reemplaza el destino de forma segura
            os.replace(tmp_path, self._file)

        except Exception as e:
            logger.error(f"Error al persistir historial: {e}")
            # Intentar limpiar el temporal si quedó
            try:
                if "tmp_path" in locals():
                    os.unlink(tmp_path)
            except Exception:
                pass

    # ─────────────────────────────────────────────
    # DIAGNÓSTICO
    # ─────────────────────────────────────────────

    def stats(self) -> dict:
        """
        Devuelve estadísticas básicas del historial.
        Útil para un comando /stats de admin.
        """
        return {
            "conversaciones_activas": len(self._data),
            "total_mensajes": sum(len(v) for v in self._data.values()),
            "archivo": str(self._file),
        }


# Instancia global — singleton compartido por handlers.py y brain.py
# Uso: from src.agent.memory import memory
memory = ConversationMemory()