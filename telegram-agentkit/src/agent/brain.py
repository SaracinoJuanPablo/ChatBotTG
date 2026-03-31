# src/agent/brain.py — Genérico, tool calling forzado para operaciones de archivo

import json
import logging
import re

from openai import AsyncOpenAI

from src.config import config
from src.agent.knowledge import knowledge_base
from src.agent.tools import TOOLS_SCHEMA, ejecutar_tool
from src.agent.intent import clasificar_mensaje

logger = logging.getLogger("agentkit")

_client = AsyncOpenAI(
    api_key=config.groq_api_key,
    base_url="https://api.groq.com/openai/v1",
)

MODELOS = [
    "llama-3.3-70b-versatile",
    "llama3-groq-70b-8192-tool-use-preview",
    "llama-3.1-8b-instant",
]


def _construir_system_prompt() -> str:
    """
    System prompt completamente genérico.
    No menciona clientes ni ningún dominio específico.
    Describe los archivos disponibles y sus contenidos reales.
    """
    archivos = knowledge_base.listar_archivos()
    lista = ", ".join(archivos) if archivos else "ninguno"
    contexto = knowledge_base.obtener_contexto_completo()

    return (
        f"Sos un asistente para gestionar archivos mediante lenguaje natural.\n\n"
        f"Archivos disponibles: {lista}\n\n"
        f"REGLAS CRÍTICAS:\n"
        f"- Para CUALQUIER operación sobre un archivo (leer, agregar, eliminar, "
        f"modificar, vaciar) SIEMPRE usá la tool correspondiente. NUNCA respondas "
        f"con texto simulando que lo hiciste.\n"
        f"- Si el usuario no menciona el nombre del archivo, deducilo del contexto "
        f"y del contenido de los archivos disponibles.\n"
        f"- Para modificar un dato existente en un Excel usá 'modificar_celda_excel', "
        f"NO 'agregar_fila_excel'.\n"
        f"- Después de ejecutar una tool, confirmá con el resultado real.\n"
        f"- Respondé en español, de forma breve.\n\n"
        f"CONTENIDO ACTUAL DE LOS ARCHIVOS:\n{contexto}"
    )


async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    if not mensaje or len(mensaje.strip()) < 2:
        return "No entendí tu mensaje. ¿Podés escribirlo de nuevo?"

    for modelo in MODELOS:
        try:
            return await _llamar_con_tools(mensaje, historial, modelo)
        except Exception as e:
            logger.warning(f"Modelo {modelo} falló: {e}. Probando siguiente...")

    return "Error de conexión. Intentá de nuevo en unos segundos."


async def _llamar_con_tools(mensaje: str, historial: list[dict], modelo: str) -> str:
    system_prompt = _construir_system_prompt()
    mensajes = historial[-6:] + [{"role": "user", "content": mensaje.strip()}]

    # Clasificar intención para saber si forzar tool_choice
    intencion = clasificar_mensaje(mensaje)

    # Si es escritura o lectura de archivo → forzar uso de tools
    # Si es chat general → dejar que el LLM decida
    if intencion in ("escritura", "lectura"):
        tool_choice = "required"
    else:
        tool_choice = "auto"

    logger.info(f"[{modelo}] intención={intencion} tool_choice={tool_choice}")

    # ── Primera llamada ───────────────────────────────────────────────────────
    r1 = await _client.chat.completions.create(
        model=modelo,
        max_tokens=512,
        temperature=0.1,
        messages=[{"role": "system", "content": system_prompt}, *mensajes],
        tools=TOOLS_SCHEMA,
        tool_choice=tool_choice,
    )

    msg  = r1.choices[0].message
    stop = r1.choices[0].finish_reason
    texto = msg.content or ""

    logger.info(f"[{modelo}] finish_reason={stop} | "
                f"tokens={r1.usage.prompt_tokens}+{r1.usage.completion_tokens}")

    if stop == "length":
        raise Exception("finish_reason=length")

    # ── Normalizar tool calls ─────────────────────────────────────────────────
    tool_calls_norm = []

    if stop == "tool_calls" and msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls_norm.append({
                "id": tc.id, "nombre": tc.function.name,
                "args": args, "raw": tc,
            })
    elif texto and "<tool_call>" in texto:
        for i, m in enumerate(re.finditer(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', texto, re.DOTALL)):
            try:
                datos = json.loads(m.group(1))
                args  = datos.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args) if args else {}
                tool_calls_norm.append({
                    "id": f"txt_{i}", "nombre": datos.get("name", ""),
                    "args": args, "raw": None,
                })
            except Exception as e:
                logger.warning(f"Error parseando tool_call texto: {e}")

    # ── Sin tools → respuesta directa ────────────────────────────────────────
    if not tool_calls_norm:
        return _limpiar(texto) or "No entendí la solicitud. ¿Podés ser más específico?"

    # ── Ejecutar tools en disco ───────────────────────────────────────────────
    resultados = []
    for tc in tool_calls_norm:
        logger.info(f"Ejecutando: {tc['nombre']}({tc['args']})")
        resultado = ejecutar_tool(tc["nombre"], tc["args"])
        logger.info(f"Resultado: {resultado[:120]}")
        resultados.append({**tc, "resultado": resultado})

    # ── Segunda llamada — confirmar con resultado real ─────────────────────────
    tiene_sdk = any(r["raw"] is not None for r in resultados)

    if tiene_sdk:
        mensajes_2 = [
            {"role": "system", "content": system_prompt},
            *mensajes,
            {
                "role": "assistant", "content": texto,
                "tool_calls": [
                    {"id": r["raw"].id, "type": "function",
                     "function": {"name": r["raw"].function.name, "arguments": r["raw"].function.arguments}}
                    for r in resultados if r["raw"] is not None
                ]
            },
            *[{"role": "tool", "tool_call_id": r["id"], "content": r["resultado"]} for r in resultados]
        ]
    else:
        resumen = "\n".join(f"- {r['nombre']}: {r['resultado']}" for r in resultados)
        mensajes_2 = [
            {"role": "system", "content": "Confirmá en español muy brevemente lo realizado."},
            {"role": "user",   "content": f"Resultados:\n{resumen}"}
        ]

    r2 = await _client.chat.completions.create(
        model=MODELOS[0],
        max_tokens=200,
        messages=mensajes_2,
    )

    respuesta_final = _limpiar(r2.choices[0].message.content or "").strip()
    if not respuesta_final:
        respuesta_final = "\n".join(r["resultado"] for r in resultados)

    logger.info(f"Respuesta final: {respuesta_final[:80]}")
    return respuesta_final


def _limpiar(texto: str) -> str:
    return re.sub(r'<tool_call>.*?</tool_call>', '', texto, flags=re.DOTALL).strip()