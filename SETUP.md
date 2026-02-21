# Amazon Launchpad - Setup Guide

## 🚨 Pre-Flight Checklist (COMPLETE THESE STEPS)

### Step 1: Database Setup (REQUIRED)

The dashboard requires a PostgreSQL database with the launchpad schema. You have two options:

#### Option A: Use Existing Database (Recommended)
If you have the `amazon-mi` database already running:

1. **Connect as superuser** and run the migrations:
```bash
# Connect to your PostgreSQL server
psql -h 192.168.0.110 -p 5433 -U postgres -d amazon_dash

# Run the 6 migrations in order
\i /mnt/amazon-launch/migrations/001_launchpad_security.sql
\i /mnt/amazon-launch/migrations/002_launchpad_core_tables.sql
\i /mnt/amazon-launch/migrations/003_launchpad_compliance.sql
\i /mnt/amazon-launch/migrations/004_launchpad_pricing.sql
\i /mnt/amazon-launch/migrations/005_launchpad_creative.sql
\i /mnt/amazon-launch/migrations/006_launchpad_api_budget.sql
```

2. **Create the database users** (if not already created by amazon-mi):
```sql
-- Create roles (run as postgres superuser)
CREATE ROLE launchpad_admin NOLOGIN;
CREATE ROLE launchpad_app LOGIN PASSWORD 'your_secure_password';
CREATE ROLE launchpad_reader LOGIN PASSWORD 'your_secure_password';

-- Grant permissions
GRANT USAGE ON SCHEMA launchpad TO launchpad_app, launchpad_reader;
GRANT CREATE ON SCHEMA launchpad TO launchpad_admin;
ALTER DEFAULT PRIVILEGES FOR ROLE launchpad_admin IN SCHEMA launchpad
  GRANT SELECT, INSERT, UPDATE ON TABLES TO launchpad_app;
ALTER DEFAULT PRIVILEGES FOR ROLE launchpad_admin IN SCHEMA launchpad
  GRANT SELECT ON TABLES TO launchpad_reader;
```

#### Option B: Fresh Database Setup
If setting up a new database:

1. **Install PostgreSQL** (if not installed):
```bash
# Debian/Ubuntu
sudo apt-get install postgresql postgresql-contrib

# Start PostgreSQL
sudo systemctl start postgresql
```

2. **Create database and users**:
```bash
sudo -u postgres psql
```

```sql
CREATE DATABASE amazon_dash;
\c amazon_dash

-- Run all 6 migrations from /mnt/amazon-launch/migrations/
-- (See Option A above)
```

### Step 2: Configure Environment Variables (REQUIRED)

Edit the `.env` file with your actual values:

```bash
cd /mnt/amazon-launch
cp .env.example .env  # If you haven't already
nano .env  # Or use your preferred editor
```

**Required variables to set:**

```env
# ===== Database Configuration =====
DB_HOST=192.168.0.110        # Your PostgreSQL host
DB_PORT=5433                  # Your PostgreSQL port
DB_NAME=amazon_dash          # Database name

# Database passwords (must be URL-encoded)
# Get the actual password from your database admin
LAUNCHPAD_DB_PASSWORD_ENCODED=your_actual_password_here
MARKET_INTEL_DB_PASSWORD_ENCODED=your_actual_password_here
PG_DB_PASSWORD_ENCODED=your_actual_password_here

# ===== Jungle Scout API (for Stage 1) =====
JUNGLESCOUT_API_KEY_NAME=your_api_key_name
JUNGLESCOUT_API_KEY=your_api_key_here

# ===== Google Generative AI (for Stage 4) =====
GOOGLE_SERVICE_ACCOUNT_JSON=./gen-lang-client-0422857398-6a11b7435ae6.json
```

### Step 3: Seed Compliance Rules (REQUIRED for Stage 2)

Once database is connected, seed the compliance rules:

```bash
cd /mnt/amazon-launch
source venv/bin/activate
python scripts/seed_compliance_rules.py
```

### Step 4: Verify Setup

Run the validation script:

```bash
cd /mnt/amazon-launch
source venv/bin/activate
psql -U launchpad_app -d amazon_dash -f scripts/validate_launchpad_access.sql
```

### Step 5: Manage Streamlit Service (Persistent)

Use the Launchpad systemd service (runs on port 8503 and survives reboots):

Do not stop or modify services on ports 8501/8502; those are owned by BI/MI systems.

```bash
# Check service status
sudo systemctl status streamlit-launchpad.service --no-pager

# Restart service after config/code changes
sudo systemctl restart streamlit-launchpad.service

# Follow live logs
sudo journalctl -u streamlit-launchpad.service -f
```

Enable on boot (one-time):

```bash
sudo systemctl enable streamlit-launchpad.service
```

---

## 🔧 Current Status

### ✅ Working
- Streamlit server running on port 8503
- Virtual environment with all dependencies
- Git repository pushed to GitHub
- Port isolation rules (8501/8502 protected)

### ⚠️ Blocked (Need Your Input)
- Database connection (needs real credentials)
- Database migrations (need to be applied)
- Compliance rules seeding (needs DB connection)

---

## 🐛 Troubleshooting

### "password authentication failed"
- Check `.env` file has correct passwords
- Ensure passwords are URL-encoded (spaces → %20, @ → %40, etc.)
- Verify database user exists: `\du` in psql

### "relation does not exist"
- Migrations haven't been applied
- Run all 6 SQL migration files

### "No module named 'psycopg'"
- Virtual environment not activated
- Run: `source venv/bin/activate`

### Port already in use
```bash
# Check what's using the port
lsof -i :8503

# Restart Launchpad service cleanly
sudo systemctl restart streamlit-launchpad.service
```

---

## 📞 Next Steps

1. **Apply database migrations** (run the 6 SQL files)
2. **Update .env with real credentials**
3. **Seed compliance rules**
4. **Test dashboard functionality**
5. **Move to Stage 2 page development**

The dashboard code is complete and ready - it just needs a working database connection to display data!
