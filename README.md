<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="expenses_organizer/frontend/src/assets/brand/costoad-logo-dark.png">
    <img src="expenses_organizer/frontend/src/assets/brand/costoad-logo.png" alt="CostOad" height="72">
  </picture>
</p>

# CostOad — AI Expenses Recognition

**Demo:** [ai-expenses-recognition-system.vercel.app](https://ai-expenses-recognition-system.vercel.app) (acceso solo con cuenta creada por el administrador)

Multi-tenant SaaS where a company drags in invoices/receipts (PDF or photo) and
Claude extracts vendor, amounts, and date via vision + structured JSON output,
auto-linking each document to a vendor and letting users organize spend by
category with summary reports. Scales from a single file to bulk batches of
50+ documents using Anthropic's Message Batches API instead of processing
everything synchronously — a deliberate architecture choice made to keep
per-document latency low without overloading a small single-worker backend.

**Stack:** Python, FastAPI, PostgreSQL (Supabase), SQLAlchemy + Alembic,
Anthropic Claude API (vision, structured outputs, Message Batches), Tesseract
OCR (optional), React, TypeScript, Vite, Supabase Auth + Storage, Docker,
Render + Vercel.

---

## Funcionalidades

- **Subida de documentos**: uno, varios o carpetas completas (arrastrar y soltar). PDFs e imágenes.
- **Extracción con Claude**: proveedor (`vendor_name`, NIT), fecha, total, impuesto, moneda, líneas de detalle y notas, como JSON estructurado.
- **Organización**: cada documento se vincula automáticamente a un proveedor y a una categoría de gasto; ambos se pueden corregir a mano.
- **Búsqueda de comprobantes**: un solo campo busca por proveedor, valor (`186.900`, `186900` o el inicio del número), categoría, nombre de archivo y fecha (`2026-03`, `15/03/2026`), sin distinguir mayúsculas ni tildes. Varias palabras se combinan (`claro 2026-03`).
- **Exportar a Excel**: descarga un `.xlsx` con los comprobantes que coinciden con la búsqueda y los filtros actuales (hoja *Egresos* con fecha, proveedor, NIT, categoría, IVA, total, moneda y alertas; hoja *Resumen* con totales por categoría y proveedor).
- **Tabla de documentos**: filtros por proveedor, categoría, rango de fechas del documento y "información faltante"; paginación; previsualización del archivo; reintento de procesamiento fallido y borrado. Los documentos con un valor sospechoso muestran un aviso ⚠ con el motivo y se pueden filtrar con "Solo con alertas de valor".
- **Vista de proveedores** con paginación.
- **Reportes**: totales por categoría y por proveedor, con gráficas.
- **Revisión de duplicados**: pestaña que, al pulsar "Revisar duplicados", agrupa los documentos que parecen el mismo egreso (por ejemplo, una factura y su comprobante de pago) y muestra cuánto se estaría contando de más. Permite previsualizar, eliminar o marcar el grupo como "No es duplicado".
- **Login** con Supabase Auth (sin registro público) y aislamiento de datos por empresa.

## Arquitectura

### Vista general

```text
            USUARIO (navegador)
                   │
                   ▼
   ┌───────────────────────────────┐
   │ FRONTEND  (React + Vite)      │  Vercel
   │ Login · Subir · Tabla ·       │
   │ Proveedores · Reportes ·      │
   │ Duplicados                    │
   └───────┬──────────────┬────────┘
           │ ① login      │ ② HTTP + token (JSON)
           ▼              ▼
   ┌──────────────┐  ┌─────────────────────────────┐
   │ SUPABASE AUTH│◄─┤ BACKEND (FastAPI, Python)   │  Render
   │ (usuarios)   │ ③│ contenedor Docker           │
   └──────────────┘  │ api → services → models     │
                     └──┬──────────┬──────────┬────┘
                        │④         │⑤         │⑥
                        ▼          ▼          ▼
                ┌────────────┐ ┌─────────┐ ┌──────────────┐
                │ PostgreSQL │ │ Storage │ │ ANTHROPIC    │
                │ (Supabase) │ │(PDF/img)│ │ Claude Haiku │
                │ vía ORM    │ │         │ │ (visión→JSON)│
                └────────────┘ └─────────┘ └──────────────┘
```

1. El usuario inicia sesión en Supabase Auth y recibe un token de acceso.
2. El frontend llama al backend con ese token en `Authorization: Bearer ...`.
3. El backend valida el token contra Supabase y obtiene el usuario y su empresa.
4. Lee y escribe datos en PostgreSQL a través del ORM (SQLAlchemy).
5. Guarda y lee los archivos originales en Supabase Storage.
6. Envía cada archivo a Claude, que devuelve los campos extraídos como JSON.

### Flujo de un documento

```text
Subir archivo ─► Supabase Storage ─► fila en `documents` (status: uploaded)
      │
      ├─ < 10 archivos ─► Claude, uno por uno (síncrono)
      └─ ≥ 10 archivos ─► Anthropic Message Batch (status: batch_queued)
                           └─ GET /documents consulta el lote e ingiere resultados
      ▼
Claude devuelve JSON: proveedor, NIT, fecha, subtotal, IVA, total, moneda, líneas
      ▼
Validación de montos (p. ej. "186.900" leído como 186,9) → alertas ⚠
      ▼
Vinculación de proveedor (busca por NIT o nombre; si no existe, lo crea)
      ▼
Categoría por defecto = la última usada con ese proveedor
      ▼
status: ai_extraction_completed ─► Tabla · Reportes · Duplicados
```

En detalle:

1. El frontend sube cada archivo (`POST /documents/upload`); se guarda en Supabase Storage.
2. Con menos de 10 archivos, cada documento se procesa de forma síncrona con Claude. Con 10 o más, se envían en un solo **Message Batch** de Anthropic (`POST /documents/batch-extract`) y `GET /documents` consulta los lotes pendientes e ingiere los resultados cuando están listos. Los lotes cuestan un 50 % menos y evitan saturar un backend de un solo worker.
3. Claude devuelve los campos estructurados, guardados en `document_extractions` (`is_final=true`) para trazabilidad.
4. Si hay proveedor, se busca un `Vendor` existente de la empresa (por NIT, o por nombre sin distinguir mayúsculas/espacios) o se crea, y se asigna la categoría por defecto del proveedor.
5. **Auto-recuperación:** los documentos que quedan atascados en `uploaded`/`processing` (p. ej. si el usuario cerró el navegador) se reanudan solos en el siguiente `GET /documents`.

### Estructura del proyecto

```text
.
├── README.md                     Este documento
├── CLAUDE.md                     Guía de trabajo y pendientes del proyecto
└── expenses_organizer/
    ├── backend/                  ── API (Python 3.12) ──
    │   ├── main.py               Punto de entrada: crea la app FastAPI, CORS y registra routers
    │   ├── Dockerfile            Imagen para Render: Python + Tesseract (spa) + Poppler;
    │   │                         al arrancar corre `alembic upgrade head` y `uvicorn`
    │   ├── requirements.txt      Dependencias de Python
    │   ├── .env.example          Plantilla de variables de entorno (sin secretos)
    │   ├── alembic.ini
    │   ├── alembic/
    │   │   ├── env.py            Conecta Alembic con los modelos y la DATABASE_URL
    │   │   └── versions/         Historial de migraciones (baseline, montos, proveedores y
    │   │                         categorías, batch_id, document_date, duplicados, RLS)
    │   └── app/
    │       ├── api/              Capa HTTP (routers delgados, sin lógica de negocio)
    │       │   ├── deps.py       get_auth_context: valida el token y resuelve usuario/empresa
    │       │   ├── documents.py  Subida, lotes, extracción, archivo, clasificar, borrar
    │       │   ├── vendors.py, expense_categories.py, reports.py, health.py
    │       ├── services/         Lógica de negocio
    │       │   ├── ai_extraction_service.py       Claude: prompt, JSON, Message Batches, montos
    │       │   ├── document_processing_service.py Orquesta OCR/IA, proveedor y categoría
    │       │   ├── document_service.py            Consultas, filtros, auto-recuperación
    │       │   ├── amount_validation_service.py   Alertas de valores sospechosos
    │       │   ├── duplicate_service.py           Detección de duplicados probables
    │       │   ├── vendor_service.py, expense_category_service.py, report_service.py
    │       │   ├── storage_service.py             Supabase Storage
    │       │   ├── auth_service.py                Verificación del token contra Supabase Auth
    │       │   └── ocr_service.py                 Tesseract + Poppler (opcional)
    │       ├── models/           Modelos ORM (una clase = una tabla): Company, CompanyMember,
    │       │                     User, Document, DocumentFile, DocumentExtraction, Vendor,
    │       │                     ExpenseCategory, DuplicateDismissal, ProcessingLog, ...
    │       ├── schemas/          Esquemas Pydantic: forma del JSON que entra y sale de la API
    │       ├── core/config.py    Configuración leída del .env (pydantic-settings)
    │       ├── db/
    │       │   ├── session.py            Engine, pool de conexiones y sesión de SQLAlchemy
    │       │   ├── create_admin_user.py  Alta de empresa + usuario (Supabase Auth + tablas propias)
    │       │   ├── seed.py               Datos semilla (empresa y categorías por defecto)
    │       │   └── reprocess_amounts.py, backfill_document_dates.py  Scripts de mantenimiento
    │       └── tests/            Pruebas pytest (unitarias e integración)
    └── frontend/                 ── Interfaz (React 19 + TypeScript + Vite) ──
        ├── index.html, vite.config.ts, tsconfig*.json, .oxlintrc.json
        ├── package.json          Dependencias y scripts (dev, build, lint)
        ├── .env.example          VITE_API_BASE_URL, VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY
        └── src/
            ├── main.tsx, App.tsx Arranque y layout principal (pestañas)
            ├── components/       Login, UploadDocument, DocumentsTable, VendorsView,
            │                     Reports, DuplicatesView, ThemeToggle, Brand
            ├── api/              client.ts (llamadas al backend con el token) y types.ts
            ├── lib/              supabaseClient.ts, theme.ts (claro/oscuro), splash.ts
            └── assets/brand/     Logo, isotipo, paleta y animaciones de CostOad
```

Carpetas generadas que no se versionan: `.venv/` (entorno virtual de Python), `node_modules/` (dependencias de npm), `dist/` (build de Vite), `__pycache__/` y `.pytest_cache/` (cachés de Python), `.env` (secretos).

**Regla de capas del backend:** `api` recibe la petición y valida la entrada → `services` aplica la lógica de negocio → `models` lee y escribe en la base. Así cada capa tiene una sola responsabilidad y la lógica se prueba sin pasar por HTTP.

### Multi-tenant y seguridad

- **Autenticación:** el backend valida el token de Supabase contra `GET {SUPABASE_URL}/auth/v1/user`, por lo que una sesión cerrada o vencida se rechaza de inmediato. No hay registro público.
- **Aislamiento por empresa:** `company_id`/`user_id` se derivan del token, nunca de parámetros del cliente. Las operaciones por ID (borrar, clasificar, ver archivo) comprueban que el recurso pertenezca a la empresa del usuario (anti-IDOR).
- **RLS:** las tablas tienen Row Level Security activa, así que la API pública de Supabase no expone datos aunque alguien tenga la anon key.
- **CORS:** el backend solo acepta peticiones del dominio del frontend configurado en `CORS_ALLOW_ORIGINS`.

### Conceptos técnicos

| Concepto | Qué es y cómo se usa aquí |
| --- | --- |
| **API REST / FastAPI** | El backend expone operaciones HTTP (GET leer, POST crear, PATCH modificar, DELETE borrar) que devuelven JSON. FastAPI valida la entrada y genera documentación interactiva en `/docs`; `uvicorn` es el servidor que la ejecuta. |
| **Docker** | Empaqueta el backend con todo lo que necesita (Python, Tesseract, Poppler, librerías) en una imagen que corre igual en cualquier máquina. Render construye la imagen desde el `Dockerfile`. |
| **ORM (SQLAlchemy)** | Traduce clases de Python a tablas SQL: se escribe `db.get(Document, id)` en vez de SQL a mano. Modelos en singular (`Document`), tablas en plural (`documents`). |
| **Alembic** | Control de versiones de la estructura de la base. Cada archivo de `alembic/versions/` es un cambio; `alembic upgrade head` deja cualquier base igual a producción y se ejecuta solo en cada despliegue. |
| **Pydantic (schemas)** | Define la forma exacta del JSON de cada endpoint. Un *model* es la tabla; un *schema* es lo que viaja por la API. |
| **Pool de conexiones / pooler** | El backend reutiliza conexiones abiertas (10 + 20 extra) para aguantar ráfagas de subidas. Se conecta por el pooler de Supabase (puerto 6543), por eso se desactivan los prepared statements de psycopg. |
| **OCR (Tesseract + Poppler)** | Convierte imágenes de texto en texto plano; Poppler pasa las páginas del PDF a imagen. Es opcional: Claude lee el documento directamente y entiende qué número es el total o el IVA. |
| **Message Batches** | API de Anthropic para enviar muchos documentos en una sola solicitud y procesarlos en segundo plano, a mitad de precio. |
| **Vite / React / TypeScript** | React arma la interfaz con componentes; TypeScript añade tipos; Vite sirve el entorno de desarrollo (`npm run dev`) y genera el build optimizado (`dist/`). Las variables `VITE_*` se incrustan al compilar. |
| **`.venv` y `__pycache__`** | `.venv` es el entorno virtual aislado con las librerías del proyecto. `__pycache__` guarda el bytecode compilado de Python para arrancar más rápido; se regenera solo. |

## Desarrollo local

### Backend

```powershell
cd expenses_organizer/backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env      # completa SUPABASE_*, DATABASE_URL y ANTHROPIC_API_KEY
alembic upgrade head
uvicorn main:app --reload
```

Si `DATABASE_URL` apunta a Supabase, usa el connection string de **Connection Pooling** (modo Transaction, puerto 6543) desde Project Settings → Database, no la conexión directa (`db.<project-ref>.supabase.co`): esa solo resuelve por IPv6 y falla en redes sin salida IPv6. Ver comentarios en `.env.example`.

### Frontend

```powershell
cd expenses_organizer/frontend
npm install
Copy-Item .env.example .env      # VITE_API_BASE_URL, VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY
npm run dev
```

### Crear usuarios

No hay registro público. Las cuentas y empresas se crean con un script interactivo (la contraseña se pide con `getpass`):

```powershell
cd expenses_organizer/backend
python -m app.db.create_admin_user
```

`python -m app.db.seed` crea datos semilla (empresa y categorías por defecto).

## Variables de entorno

| Variable | Dónde | Descripción |
| --- | --- | --- |
| `DATABASE_URL` | backend | Connection string de PostgreSQL (pooler de Supabase, esquema `postgresql+psycopg://`) |
| `SUPABASE_URL` | backend | URL del proyecto Supabase |
| `SUPABASE_SERVICE_ROLE_KEY` | backend | Clave de servicio (Storage y verificación de tokens). **Secreta** |
| `SUPABASE_STORAGE_BUCKET` | backend | Bucket de archivos (`documents`) |
| `ANTHROPIC_API_KEY` | backend | API key de [console.anthropic.com](https://console.anthropic.com). **Secreta** |
| `ANTHROPIC_MODEL` | backend | Modelo de extracción (por defecto `claude-haiku-4-5`) |
| `CORS_ALLOW_ORIGINS` | backend | Orígenes permitidos, separados por coma; en producción debe incluir la URL de Vercel |
| `OCR_LANGUAGES`, `TESSERACT_CMD`, `POPPLER_PATH`, `TESSDATA_PREFIX` | backend | Configuración del OCR opcional |
| `VITE_API_BASE_URL` | frontend | URL del backend |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | frontend | Credenciales públicas de Supabase (se incrustan al compilar: redesplegar tras cambiarlas) |

Nunca subas `.env` al repositorio.

## API

Todos los endpoints, salvo `/health*`, requieren `Authorization: Bearer <access_token de Supabase>`.

| Método y ruta | Descripción |
| --- | --- |
| `GET /health`, `GET /health/db` | Estado del servicio y de la conexión a la base de datos |
| `POST /documents/upload` | Sube un archivo |
| `POST /documents/batch-extract` | Envía varios documentos como un Anthropic Message Batch |
| `POST /documents/{id}/ai-extract` | (Re)ejecuta la extracción con Claude |
| `POST /documents/{id}/ocr` | Ejecuta OCR local opcional (Tesseract) y luego la extracción |
| `GET /documents` | Lista paginada. Filtros: `vendor_id`, `expense_category_id`, `document_date_from`, `document_date_to`, `missing_info`, `amount_alert`, `search`, `limit`, `offset`. Cada documento trae `amount_warnings` |
| `GET /documents/export` | Descarga un `.xlsx` con todos los documentos que cumplen los mismos filtros de `GET /documents` (sin paginar) |
| `GET /documents/{id}/file` | Descarga/previsualiza el archivo |
| `PATCH /documents/{id}/classify` | Asigna o corrige `vendor_id` y/o `expense_category_id` |
| `DELETE /documents/{id}` | Borra el documento, sus filas relacionadas y el archivo |
| `GET`, `POST /vendors`; `PATCH /vendors/{id}` | Proveedores |
| `GET`, `POST /expense-categories`; `PATCH`, `DELETE /expense-categories/{id}` | Categorías de gasto (Transporte, Suministros, Inventario, Servicios Públicos, Nómina, Arriendo, Otros por defecto) |
| `GET /reports/expense-summary` | Resumen de gastos (ver abajo) |
| `GET /reports/probable-duplicates` | Grupos de documentos probablemente duplicados (ver abajo). Filtro: `date_window_days` (0-30, por defecto 5) |
| `POST /reports/probable-duplicates/dismiss` | Marca un grupo (`document_ids`) como "no es duplicado" para que no vuelva a aparecer |

### Reportes

`GET /reports/expense-summary` (acepta `vendor_id` y `expense_category_id`) devuelve:

- `rows`: una fila por documento (`document_date`, `vendor_name`, `expense_category_name`, `currency`, `total_amount`, `tax_amount`), lista para `pd.DataFrame(response.json()["rows"])`.
- `totals_by_category` y `totals_by_vendor`: montos ya sumados.
- `grand_total` y `document_count`.

Los montos salen de la extracción final de Claude; los documentos sin extracción aparecen con `total_amount=null` y no suman a los totales.

### Detección de duplicados

`GET /reports/probable-duplicates` compara los documentos de la empresa que tienen valor (`total_amount > 0`) y los agrupa cuando coinciden en:

- **Valor y moneda**: idénticos al centavo.
- **Proveedor**: el mismo (por ID o NIT) o uno de nombre similar; se ignoran tildes, mayúsculas y sufijos como S.A.S. o Ltda. Dos proveedores claramente distintos no se agrupan.
- **Fecha del documento**: a no más de `date_window_days` días entre sí.

Si a un documento le falta el proveedor o la fecha, puede aparecer igual pero con menos confianza; si faltan ambos, no se compara. Cada grupo trae una `confidence` (`alta`, `media`, `baja`), las `reasons` de la coincidencia y `possible_overcount` (el monto que se cuenta de más si todos son el mismo egreso). Tres o más documentos del mismo egreso salen en un solo grupo.

`POST /reports/probable-duplicates/dismiss` guarda cada par del grupo en `duplicate_dismissals`; esos pares dejan de marcarse (y se borran solos si se elimina el documento). Verifica que todos los documentos pertenezcan a la empresa del usuario.

## Extracción con Claude

Claude es el único paso que produce los campos estructurados, por lo que corre en todos los documentos. Con `claude-haiku-4-5` ($1/$5 por 1M tokens) el costo ronda los $0.003–0.004 USD por documento. Si hay errores en documentos difíciles, prueba `claude-sonnet-5-5` u otro modelo más capaz cambiando `ANTHROPIC_MODEL`.

### Formato de los montos

En los documentos colombianos el punto separa los miles (`186.900` son ciento ochenta y seis mil novecientos), pero leído como número en inglés daría 186,9. Para que la lectura no dependa de que el modelo adivine el formato:

- Claude devuelve, además del número, el monto **tal como está impreso** (`total_amount_text`, `tax_amount_text`), y el prompt explica el formato colombiano.
- `parse_amount_text` (`ai_extraction_service.py`) convierte ese texto con una regla fija: si aparecen punto y coma, el último es el decimal; un separador repetido o seguido de exactamente 3 dígitos es de miles (`186.900` y `186,900` dan 186900); cualquier otro caso es decimal (`186,9`).
- `resolve_amount` es el único punto que decide el valor: usa el texto impreso y, si falta o no se puede leer, el número de Claude (así siguen funcionando las extracciones anteriores a este cambio). Lo usan el guardado, los reportes y el script de relleno.

### Alertas de valor

`amount_validation_service.py` revisa si el valor extraído es plausible y devuelve avisos en `amount_warnings` (en la tabla de documentos, con el filtro `amount_alert`). Se calculan al listar, a partir de lo guardado: no hay estado ni columna extra, y la alerta desaparece sola si el documento se reprocesa y queda bien. Son pistas para mirar el documento, no pruebas de error:

- Valor menor a $1.000 COP (suele faltar la parte de los miles) o COP con decimales.
- La suma de las líneas no coincide con el total (con tolerancia de redondeo, con o sin impuesto).
- Impuesto mayor que el total.
- Moneda no detectada.

### Corrección masiva de valores mal leídos

`python -m app.db.reprocess_amounts` vuelve a extraer los documentos en COP con total menor a 1.000 o con decimales y actualiza **solo** `total_amount` y `currency` (no toca proveedor, categoría ni fecha, para no pisar correcciones manuales). Conserva lo anterior en un archivo de respaldo (`--backup`), en `processing_logs` (paso `amount_reprocess`) y en la extracción previa, que queda como no final. Opciones: `--dry-run` (no escribe), `--limit N` (muestra aleatoria). Es seguro repetirlo: los documentos ya corregidos dejan de ser candidatos. Cada documento cuesta una llamada a Claude (~$0.004 USD).

## OCR local (opcional)

El OCR con Tesseract ya no forma parte del flujo por defecto; queda como acción manual (`POST /documents/{id}/ocr`) que guarda el texto crudo y un `confidence_score` como señal de calidad. Requiere Tesseract y Poppler:

```powershell
winget install --id UB-Mannheim.TesseractOCR -e
winget install --id oschwartz10612.Poppler -e
```

Si quedan en el PATH no hace falta configurar nada más; si no, define `TESSERACT_CMD`, `POPPLER_PATH` y `TESSDATA_PREFIX` en `.env`. `OCR_LANGUAGES` controla el idioma (por defecto `spa+eng`); el traineddata de `spa` hay que descargarlo aparte desde [tesseract-ocr/tessdata](https://github.com/tesseract-ocr/tessdata) si no está en tu `tessdata`. En producción, el `Dockerfile` ya instala Tesseract (con español) y Poppler.

## Migraciones (Alembic)

```powershell
cd expenses_organizer/backend
alembic revision --autogenerate -m "descripcion del cambio"
alembic upgrade head
```

## Pruebas

```powershell
cd expenses_organizer/backend
python -m pytest
```

Las pruebas de integración usan la base de datos configurada en `.env`.

## Despliegue

- **Backend: Render**, con el `Dockerfile` (Root Directory `expenses_organizer/backend`). Al arrancar ejecuta `alembic upgrade head` y luego `uvicorn`. Variables de entorno según la tabla de arriba.
- **Frontend: Vercel**, build estático de Vite (Root Directory `expenses_organizer/frontend`).
- **Base de datos y archivos: Supabase** (PostgreSQL + Storage + Auth). En el plan gratuito, Supabase pausa los proyectos inactivos; si el backend deja de responder, revisa primero que el proyecto esté activo.
- Al cambiar `CORS_ALLOW_ORIGINS` o las variables `VITE_*`, redespliega el servicio correspondiente.
