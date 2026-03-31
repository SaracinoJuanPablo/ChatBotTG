# src/agent/intent.py
# Detección de intención GENÉRICA — sin asumir contenido de archivos.
#
# RESPONSABILIDAD ÚNICA:
#   Determinar si el mensaje del usuario es una operación de archivo
#   (leer, modificar, agregar, eliminar) o una pregunta/conversación.
#   NO ejecuta nada — solo clasifica y extrae el archivo si está mencionado.
#
# El LLM maneja la ejecución real con tool calling.
# Esta capa solo ayuda a forzar tool_choice="required" cuando corresponde.

import re
import logging

logger = logging.getLogger("agentkit")

# Verbos que indican operación de escritura sobre un archivo
_VERBOS_ESCRITURA = [
    r"elimin[aá]", r"borr[aá]", r"quit[aá]", r"remov[eé]", r"supr[ií]m[eé]",
    r"agreg[aá]", r"a[ñn]ad[eí]", r"ingres[aá]", r"registr[aá]", r"sum[aá]",
    r"carg[aá]", r"guard[aá]", r"insert[aá]",
    r"modific[aá]", r"actualiz[aá]", r"cambi[aá]", r"edit[aá]", r"correg[ií]",
    r"reemplaz[aá]", r"ponele", r"poné", r"escribí", r"escribi",
    r"vaci[aá]", r"limp[ií]a", r"borr[aá] todo", r"elimin[aá] todo",
    r"da(le)? de alta", r"da(le)? de baja",
    r"renombr[aá]", r"mové", r"mov[eé]",
]

_PATRON_ESCRITURA = re.compile(
    "|".join(_VERBOS_ESCRITURA),
    flags=re.IGNORECASE
)

# Verbos que indican operación de lectura
_VERBOS_LECTURA = [
    r"mostr[aá]", r"ense[ñn][aá]", r"list[aá]", r"ve[ré]", r"lee", r"le[eé]",
    r"dame", r"dec[ií]me", r"qu[eé] (hay|tiene|dice|contiene)",
    r"cu[aá]les (son|hay|tiene)", r"cu[aá]ntos", r"cu[aá]ntas",
    r"abr[ií]", r"mostrá", r"muéstrame", r"muestrame",
]

_PATRON_LECTURA = re.compile(
    "|".join(_VERBOS_LECTURA),
    flags=re.IGNORECASE
)


def clasificar_mensaje(mensaje: str) -> str:
    """
    Clasifica el mensaje en una de tres categorías:
    - "escritura": el usuario quiere modificar un archivo → tool_choice="required"
    - "lectura":   el usuario quiere ver un archivo → tool_choice="required"
    - "chat":      pregunta o conversación → tool_choice="auto"
    """
    if _PATRON_ESCRITURA.search(mensaje):
        logger.info(f"[INTENT] clasificado como: escritura")
        return "escritura"
    if _PATRON_LECTURA.search(mensaje):
        logger.info(f"[INTENT] clasificado como: lectura")
        return "lectura"
    logger.info(f"[INTENT] clasificado como: chat")
    return "chat"