# Holded New Orders Mailer

Script en Python que **detecta pedidos de venta (Sales Orders) en Holded** y envía un **correo de reserva de material** con una tabla de líneas.  
Funciona en local y/o de forma automática con **GitHub Actions** (cada 5 minutos), evitando duplicados con un **archivo de estado** (`.state/processed_salesorders.json`).

---

## 🚀 Qué hace

- Consulta pedidos de Holded (por `--doc-id`, por ventana de minutos o por días).
- Mapea líneas de producto e infiere:
  - **Potencia (W)** desde atributos o texto (p. ej., `605W`, `A605`).
  - **Precio** dinámico:
    - Si hay potencia → **€/W**.
    - Si NO hay potencia → **€/ud**.
  - **Pallets** (solo si hay potencia), con reglas configurables.
- Detecta la línea de **transporte por nombre**: `Transporte`, `Shipping cost`, `Shipping`, `Transport`, `Flete`, `Portes`, `Envío`  
  (solo muestra transporte **en la primera fila**).
- Envía email con **asunto dinámico**:
  - Si hay pallets: `VENDIDO {n} pallets {material} a {Cliente}`
  - Si no hay pallets: `VENDIDO {n} uds {material} a {Cliente}`
- Mantiene **estado** de pedidos ya procesados (no reenvía).

---

## 📦 Requisitos

- Python **3.11+**
- Cuenta SMTP (si usas Gmail, **contraseña de aplicación** y `MAIL_FROM = SMTP_USER`)
- Clave de API de Holded

---

## 🗂️ Estructura del proyecto (ejemplo)


## 🧩 Instalación (local)

Instala dependencias:
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```
Crea un .env (solo para ejecución local; en GitHub Actions se usan Secrets):

```dotenv
# Holded
HOLDED_API_KEY=tu_api_key
HOLDED_USE_BEARER=false   # o true si tu API usa Bearer

# SMTP
MAIL_FROM=tu_correo@dominio.com
MAIL_TO=dest1@dom.com,dest2@dom.com
SMTP_HOST=smtp.dominio.com
SMTP_PORT=587             # 587 STARTTLS | 465 SSL
SMTP_USER=tu_correo@dominio.com
SMTP_PASS=tu_contraseña_o_app_password
```
**Nota (Gmail):** usa una contraseña de aplicación y asegúrate de que `MAIL_FROM = SMTP_USER`.
