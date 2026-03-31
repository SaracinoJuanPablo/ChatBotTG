# src/bot/handlers.py
# Handlers de Telegram — parsean comandos y delegan a los módulos correctos.
#
# PRINCIPIO DE DISEÑO:
#   Cada handler hace exactamente una cosa:
#   1. Parsear el input del usuario
#   2. Validar permisos si es comando admin
#   3. Llamar al módulo responsable (knowledge.py o brain.py)
#   4. Responder al usuario con el resultado
#
#   Ningún handler toca archivos directamente — eso es trabajo de knowledge.py.
#   Ningún handler llama a OpenRouter directamente — eso es trabajo de brain.py.
#
# COMANDOS PÚBLICOS (cualquier usuario):
#   /start          — bienvenida
#   /archivos       — lista archivos de knowledge disponibles
#   /ver [archivo]  — muestra contenido de un archivo
#
# COMANDOS ADMIN (solo ADMIN_USER_ID del .env):
#   /agregar [archivo] [texto]     — agrega línea al final del archivo
#   /eliminar [archivo] [texto]    — elimina líneas que contengan el texto
#   /editar [archivo]              — reemplaza contenido completo (siguiente msg)
#   /reset                         — borra historial de la conversación actual
#   /stats                         — estadísticas del bot

import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode

from src.config import config
from src.agent.brain import generar_respuesta
from src.agent.knowledge import knowledge_base
from src.agent.memory import memory

logger = logging.getLogger("agentkit")

# Estado temporal para el comando /editar (espera el contenido en el siguiente mensaje)
# { chat_id: nombre_archivo }
_esperando_contenido_editar: dict[int, str] = {}


# ─────────────────────────────────────────────
# UTILIDADES INTERNAS
# ─────────────────────────────────────────────

def _es_admin(user_id: int) -> bool:
    """Verifica si el user_id es el admin configurado en .env."""
    return user_id == config.admin_user_id


def _escapar_md(texto: str) -> str:
    """
    Escapa caracteres especiales para Markdown V2 de Telegram.
    Solo se usa cuando necesitamos formato especial en la respuesta.
    Para respuestas del LLM usamos texto plano para evitar errores de parseo.
    """
    caracteres = r"\_*[]()~`>#+-=|{}.!"
    for c in caracteres:
        texto = texto.replace(c, f"\\{c}")
    return texto


async def _responder(update: Update, texto: str, markdown: bool = False):
    """
    Envía una respuesta al usuario.
    Por defecto texto plano — evita errores de parseo de Markdown
    cuando la respuesta viene del LLM (que puede incluir caracteres especiales).
    """
    parse_mode = ParseMode.MARKDOWN if markdown else None
    # Telegram limita mensajes a 4096 caracteres
    if len(texto) > 4096:
        texto = texto[:4090] + "\n[...]"
    await update.message.reply_text(texto, parse_mode=parse_mode)


# ─────────────────────────────────────────────
# COMANDOS PÚBLICOS
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start — Bienvenida.
    Informa qué puede hacer el bot y si el usuario es admin.
    """
    user = update.effective_user
    es_admin = _es_admin(user.id)

    mensaje = (
        f"Hola {user.first_name}! 👋\n\n"
        "Soy el asistente del equipo. Podés preguntarme lo que necesites "
        "y voy a responder con la información disponible.\n\n"
        "Comandos disponibles:\n"
        "  /archivos — ver archivos de knowledge\n"
        "  /ver [archivo] — ver contenido de un archivo\n"
    )

    if es_admin:
        mensaje += (
            "\nComandos de administración:\n"
            "  /agregar [archivo] [texto] — agregar línea\n"
            "  /eliminar [archivo] [texto] — eliminar líneas\n"
            "  /editar [archivo] — reemplazar contenido completo\n"
            "  /reset — borrar historial de esta conversación\n"
            "  /stats — estadísticas del bot\n"
        )

    await _responder(update, mensaje)
    logger.info(f"Comando /start — user_id={user.id} admin={es_admin}")


async def cmd_archivos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /archivos — Lista los archivos disponibles en knowledge base.
    Cualquier usuario puede verlos.
    """
    archivos = knowledge_base.listar_archivos()

    if not archivos:
        await _responder(update, "No hay archivos de knowledge disponibles todavía.")
        return

    lista = "\n".join(f"  • {nombre}" for nombre in archivos)
    await _responder(update, f"Archivos disponibles:\n{lista}\n\nUsá /ver [nombre] para ver el contenido.")


async def cmd_ver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /ver [archivo] — Muestra el contenido de un archivo.
    Cualquier usuario puede usarlo.

    Ejemplo: /ver clientes.txt
    """
    if not context.args:
        await _responder(update, "Uso: /ver [nombre_archivo]\nEjemplo: /ver clientes.txt")
        return

    nombre = context.args[0].strip()
    contenido = knowledge_base.ver_archivo(nombre)

    if contenido is None:
        archivos = knowledge_base.listar_archivos()
        sugerencia = f"\nArchivos disponibles: {', '.join(archivos)}" if archivos else ""
        await _responder(update, f"No encontré el archivo '{nombre}'.{sugerencia}")
        return

    if not contenido.strip():
        await _responder(update, f"El archivo '{nombre}' existe pero está vacío.")
        return

    # Truncar si es muy largo para Telegram
    encabezado = f"📄 {nombre}:\n\n"
    cuerpo = contenido
    if len(encabezado + cuerpo) > 4096:
        cuerpo = cuerpo[:4000] + "\n[... archivo truncado ...]"

    await _responder(update, encabezado + cuerpo)


# ─────────────────────────────────────────────
# COMANDOS ADMIN — escritura en disco
# ─────────────────────────────────────────────

async def cmd_agregar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /agregar [archivo] [texto a agregar]
    Agrega una línea al final del archivo.

    Ejemplo: /agregar clientes.txt Pedro Lopez
    La línea "Pedro Lopez" se agrega al final de clientes.txt EN DISCO.
    """
    user_id = update.effective_user.id
    if not _es_admin(user_id):
        await _responder(update, "No tenés permisos para usar este comando.")
        return

    # Necesitamos al menos 2 argumentos: archivo y texto
    if not context.args or len(context.args) < 2:
        await _responder(
            update,
            "Uso: /agregar [archivo] [texto]\n"
            "Ejemplo: /agregar clientes.txt Pedro Lopez"
        )
        return

    nombre_archivo = context.args[0].strip()
    # El texto puede tener espacios — unimos todo lo que viene después del archivo
    texto = " ".join(context.args[1:]).strip()

    # Delegar a knowledge.py — que escribe en disco y actualiza el cache
    exito, mensaje = knowledge_base.agregar_linea(nombre_archivo, texto)

    await _responder(update, mensaje)
    logger.info(
        f"/agregar — admin={user_id} archivo={nombre_archivo} "
        f"texto='{texto}' exito={exito}"
    )


async def cmd_eliminar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /eliminar [archivo] [texto a eliminar]
    Elimina todas las líneas que contengan el texto (case-insensitive).

    Ejemplo: /eliminar clientes.txt Sofia
    → Elimina de clientes.txt EN DISCO todas las líneas que contengan "Sofia".
    → El LLM ya no verá a Sofia en el próximo mensaje.
    """
    user_id = update.effective_user.id
    if not _es_admin(user_id):
        await _responder(update, "No tenés permisos para usar este comando.")
        return

    if not context.args or len(context.args) < 2:
        await _responder(
            update,
            "Uso: /eliminar [archivo] [texto]\n"
            "Ejemplo: /eliminar clientes.txt Sofia"
        )
        return

    nombre_archivo = context.args[0].strip()
    texto = " ".join(context.args[1:]).strip()

    # Delegar a knowledge.py — escribe en disco, hace backup, actualiza cache
    exito, mensaje = knowledge_base.eliminar_lineas(nombre_archivo, texto)

    await _responder(update, mensaje)
    logger.info(
        f"/eliminar — admin={user_id} archivo={nombre_archivo} "
        f"texto='{texto}' exito={exito}"
    )


async def cmd_editar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /editar [archivo]
    Pone el bot en modo edición — espera el contenido completo en el siguiente mensaje.

    Flujo:
    1. Admin escribe: /editar clientes.txt
    2. Bot responde: "Enviá el nuevo contenido completo para clientes.txt"
    3. Admin escribe el contenido nuevo (puede ser multilínea)
    4. Bot reemplaza el archivo en disco y confirma

    Esto permite editar archivos completos sin limitaciones de longitud de comando.
    """
    user_id = update.effective_user.id
    if not _es_admin(user_id):
        await _responder(update, "No tenés permisos para usar este comando.")
        return

    if not context.args:
        await _responder(
            update,
            "Uso: /editar [archivo]\n"
            "Ejemplo: /editar clientes.txt\n"
            "Luego enviá el contenido nuevo completo."
        )
        return

    nombre_archivo = context.args[0].strip()

    # Validar que el archivo sea accesible antes de entrar en modo espera
    archivos_disponibles = knowledge_base.listar_archivos()
    # Permitir editar archivos existentes O crear nuevos con extensión válida
    extension = "." + nombre_archivo.split(".")[-1] if "." in nombre_archivo else ""
    if nombre_archivo not in archivos_disponibles and extension not in config.allowed_extensions:
        await _responder(
            update,
            f"Nombre de archivo inválido: '{nombre_archivo}'\n"
            f"Extensiones permitidas: {', '.join(config.allowed_extensions)}"
        )
        return

    # Guardar estado: este chat está esperando el contenido del archivo
    chat_id = update.effective_chat.id
    _esperando_contenido_editar[chat_id] = nombre_archivo

    await _responder(
        update,
        f"Modo edición activado para `{nombre_archivo}`.\n"
        f"Enviá el nuevo contenido completo en el próximo mensaje.\n"
        f"(El contenido actual será reemplazado por lo que escribas.)"
    )
    logger.info(f"/editar — admin={user_id} esperando contenido para {nombre_archivo}")


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /reset — Borra el historial de conversación del chat actual.
    Solo admin. Útil para reiniciar el contexto cuando el bot empieza
    a "confundirse" por un historial muy largo.
    """
    user_id = update.effective_user.id
    if not _es_admin(user_id):
        await _responder(update, "No tenés permisos para usar este comando.")
        return

    chat_id = update.effective_chat.id
    memory.limpiar_historial(chat_id)
    await _responder(update, "Historial de esta conversación borrado. Empezamos de cero.")
    logger.info(f"/reset — admin={user_id} chat_id={chat_id}")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /stats — Muestra estadísticas del bot.
    Solo admin.
    """
    user_id = update.effective_user.id
    if not _es_admin(user_id):
        await _responder(update, "No tenés permisos para usar este comando.")
        return

    stats = memory.stats()
    archivos = knowledge_base.listar_archivos()

    mensaje = (
        f"📊 Estadísticas del bot\n\n"
        f"Conversaciones activas: {stats['conversaciones_activas']}\n"
        f"Total mensajes en historial: {stats['total_mensajes']}\n"
        f"Archivos de knowledge: {len(archivos)}\n"
        f"  {chr(10).join('• ' + a for a in archivos) if archivos else '  (ninguno)'}\n"
        f"Modelo LLM: {config.llm_model}\n"
    )

    await _responder(update, mensaje)


# ─────────────────────────────────────────────
# HANDLER DE MENSAJES DE TEXTO
# ─────────────────────────────────────────────

async def handle_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja todos los mensajes de texto que no son comandos.

    Tiene dos modos:
    A) Modo edición: si el chat está esperando contenido para /editar,
       toma el mensaje como el nuevo contenido del archivo.
    B) Modo conversación: procesa el mensaje con el LLM.
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    texto = update.message.text.strip()

    if not texto:
        return

    # ── MODO A: Esperando contenido para /editar ──────────────────
    if chat_id in _esperando_contenido_editar:
        # Solo el admin puede completar el /editar
        if not _es_admin(user_id):
            del _esperando_contenido_editar[chat_id]
            return

        nombre_archivo = _esperando_contenido_editar.pop(chat_id)

        exito, mensaje = knowledge_base.reemplazar_contenido(nombre_archivo, texto)

        if exito:
            await _responder(
                update,
                f"{mensaje}\n\nEl LLM ya usará el contenido actualizado en el próximo mensaje."
            )
        else:
            await _responder(update, f"Error al editar: {mensaje}")

        logger.info(
            f"Edición completada — admin={user_id} "
            f"archivo={nombre_archivo} exito={exito}"
        )
        return

    # ── MODO B: Conversación normal con el LLM ────────────────────
    # Mostrar "escribiendo..." mientras procesa
    await context.bot.send_chat_action(
        chat_id=chat_id,
        action="typing"
    )

    # Obtener historial previo de memory.py
    historial = memory.obtener_historial(chat_id)

    # Generar respuesta con brain.py (OpenRouter + knowledge hot-reload)
    respuesta = await generar_respuesta(texto, historial)

    # Guardar el turno completo en memory.py (escritura atómica)
    memory.guardar_turno(chat_id, texto, respuesta)

    # Responder al usuario
    await _responder(update, respuesta)

    logger.info(
        f"Mensaje procesado — chat_id={chat_id} "
        f"chars_entrada={len(texto)} chars_salida={len(respuesta)}"
    )


# ─────────────────────────────────────────────
# REGISTRO DE HANDLERS
# ─────────────────────────────────────────────

def registrar_handlers(app: Application):
    """
    Registra todos los handlers en la aplicación de Telegram.
    Se llama desde main.py al iniciar el bot.

    Orden de registro importa:
    - Los CommandHandlers van primero (tienen prioridad sobre MessageHandler)
    - El MessageHandler de texto va último (captura todo lo que no es comando)
    """
    # Comandos públicos
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("archivos", cmd_archivos))
    app.add_handler(CommandHandler("ver",      cmd_ver))

    # Comandos admin
    app.add_handler(CommandHandler("agregar",  cmd_agregar))
    app.add_handler(CommandHandler("eliminar", cmd_eliminar))
    app.add_handler(CommandHandler("editar",   cmd_editar))
    app.add_handler(CommandHandler("reset",    cmd_reset))
    app.add_handler(CommandHandler("stats",    cmd_stats))

    # Mensajes de texto normales (conversación con el LLM)
    # filters.TEXT & ~filters.COMMAND: solo texto, excluye comandos
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mensaje)
    )

    logger.info("Handlers registrados correctamente")