# 🤖 ChatBotTG (Telegram AgentKit)

ChatBotTG es un bot de Telegram impulsado por inteligencia artificial (LLMs) cuyo objetivo principal es administrar y consultar archivos de manera remota mediante lenguaje natural. 

El bot es capaz de ejecutar acciones directas sobre archivos persistentes (`.txt`, `.md`, `.xlsx`, `.docx`) directamente desde tu chat de Telegram, transformando la aplicación en tu asistente personal para el manejo de información y tareas de ofimática.

---

## ✨ Características Principales

- **Chat en Lenguaje Natural**: Conversa de forma fluida utilizando modelos avanzados (por ejemplo: `nvidia/llama-3.1-nemotron-70b-instruct` u otros modelos de Groq/OpenRouter).
- **Gestión Avanzada de Archivos Locales**:
  - 📝 **Archivos de texto (.txt, .md)**: Lee archivos, agrega líneas, busca y elimina información, o reemplaza contenido completo.
  - 📊 **Planillas Excel (.xlsx, .xlsm)**: Lee datos de hojas específicas, añade nuevas filas, modifica celdas en particular según criterios de búsqueda, o elimina registros.
  - 📄 **Documentos Word (.docx)**: Lee el contenido completo, añade nuevos párrafos al final del documento o realiza búsqueda y reemplazo de textos existentes.
- **Funcionamiento por Polling (Ideal uso local)**: No requiere de IPs públicas, túneles como ngrok, ni webhooks de Telegram. El bot puede operar de forma segura desde tu propia computadora consultando si hay nuevos mensajes de manera periódica.
- **Historial y Logs**: Mantiene consistencia de la conversación en curso en memoria, y guarda registro de toda su actividad en un archivo log rotativo (`bot.log`).
- **Despliegue Simple en Docker**: Incorpora su propio `Dockerfile` y `docker-compose.yml` para una ejecución modular, segura y sin conflictos.

---

## 🚀 Instalación y Configuración

### Requisitos Previos

- [Docker](https://www.docker.com/) y [Docker Compose](https://docs.docker.com/compose/) (Alternativamente: Python 3.10+)
- Un Token de Bot de Telegram (Obtenible iniciando un chat con [@BotFather](https://t.me/botfather))
- Claves de API de LLMs como [Groq](https://console.groq.com/) o [OpenRouter](https://openrouter.ai/).

### Pasos para iniciar

1. **Clonar este repositorio y acceder a la carpeta del bot:**
   ```bash
   git clone https://github.com/SaracinoJuanPablo/ChatBotTG.git
   cd ChatBotTG/telegram-agentkit
   ```

2. **Configurar entorno:**
   Crea o modifica el archivo `.env` dentro de la carpeta `telegram-agentkit` ajustando tus credenciales:
   ```env
   # Accesos de Telegram
   TELEGRAM_BOT_TOKEN=tu_token_de_botfather
   ADMIN_USER_ID=tu_user_id_de_telegram
   
   # Configuraciones de IA 
   GROQ_API_KEY=tu_clave_de_groq
   GEMINI_API_KEY=tu_clave_de_gemini
   LLM_MODEL=nvidia/llama-3.1-nemotron-70b-instruct
   
   # Rutas locales (Por defecto, en Docker, montará en la carpeta /app/data)
   APP_NAME=mi-bot
   KNOWLEDGE_DIR=./data/knowledge
   ```
   *Nota: Si ejecutas de manera nativa (sin Docker) puede que debas colocar la ruta absoluta de Windows/Linux hacia tu carpeta "knowledge"*.

3. **Iniciar el bot:**
   - **Opción con Docker (Recomendada)**: 
     ```bash
     docker-compose up --build -d
     ```
   - **Opción Nativa (Solo Python)**:
     ```bash
     pip install -r requirements.txt
     python -m src.main
     ```

---

## 🛠️ Cómo Utilizarlo

Una vez que el terminal (o el entorno Docker) muestre que el Bot ha arrancado correctamente:
1. Contacta a tu bot en Telegram usando el nombre que asignaste en BotFather.
2. Inicia un chat dándole una directiva en lenguaje natural. Ejemplos de lo que puedes hacer:
   - *"Lee mi archivo tareas.txt y dime qué tengo pendiente."*
   - *"Agrega una nueva fila al Excel de 'ventas.xlsx' con los valores: 25/10/2023, Servicio de IT, $1500."*
   - *"Reemplaza en mi documento 'contrato.docx' la palabra '[CLIENTE]' por 'Empresa ACME'."*

Todos tus archivos deben depositarse manualmente (o ser creados por el bot) en la carpeta local `/data/knowledge/` para que el bot tenga permisos de accederlos.
