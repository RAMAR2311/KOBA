from app import create_app
from models import db

def main():
    app = create_app()
    with app.app_context():
        # 1. Asegurar creación de tablas si faltara alguna (ej. providers)
        db.create_all()

        # 2. Columnas añadidas a los modelos que necesitan existir en PostgreSQL
        columnas = [
            # Tabla maneos
            "ALTER TABLE maneos ADD COLUMN IF NOT EXISTS valor_fijo NUMERIC(10, 2);",
            "ALTER TABLE maneos ADD COLUMN IF NOT EXISTS variant_id INTEGER;",
            "ALTER TABLE maneos ADD COLUMN IF NOT EXISTS cliente_id INTEGER;",
            # Tabla product_variants (precios específicos para subcategorías)
            "ALTER TABLE product_variants ADD COLUMN IF NOT EXISTS precio_costo NUMERIC(10, 2);",
            "ALTER TABLE product_variants ADD COLUMN IF NOT EXISTS precio_minimo NUMERIC(10, 2);",
            "ALTER TABLE product_variants ADD COLUMN IF NOT EXISTS precio_sugerido NUMERIC(10, 2);",
            # Tabla users
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS telefono VARCHAR(20);",
            # Tabla sale_details
            "ALTER TABLE sale_details ADD COLUMN IF NOT EXISTS variant_id INTEGER;",
            "ALTER TABLE sale_details ADD COLUMN IF NOT EXISTS nombre_manual VARCHAR(200);",
            "ALTER TABLE sale_details ADD COLUMN IF NOT EXISTS precio_costo_manual NUMERIC(10, 2);",
            # Tabla facturas_bodega_detalles
            "ALTER TABLE facturas_bodega_detalles ADD COLUMN IF NOT EXISTS variant_id INTEGER;",
            "ALTER TABLE facturas_bodega_detalles ADD COLUMN IF NOT EXISTS precio_venta NUMERIC(10, 2);",
            # Tabla clientes
            "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS creado_por_id INTEGER;",
            "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS contacto_persona VARCHAR(100);",
            "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS local_numero VARCHAR(50);",
            "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS notas TEXT;",
            "ALTER TABLE clientes ALTER COLUMN documento_o_nit DROP NOT NULL;",
            "ALTER TABLE clientes ALTER COLUMN telefono DROP NOT NULL;",
        ]

        for sql in columnas:
            try:
                db.session.execute(db.text(sql))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"[Aviso] {sql} -> {e}")

        # 3. Ajustar valores antiguos registrados con números abreviados (ej: 30 -> 30000)
        try:
            db.session.execute(db.text("UPDATE maneos SET valor_fijo = valor_fijo * 1000 WHERE valor_fijo > 0 AND valor_fijo < 1000;"))
            db.session.commit()
        except Exception as e:
            db.session.rollback()

        # 4. Vincular automáticamente maneos existentes que tenían solo nombre de texto a Clientes
        try:
            from models import Cliente, Maneo
            maneos_sin_cliente = Maneo.query.filter(Maneo.cliente_id.is_(None)).all()
            for m in maneos_sin_cliente:
                if m.local_vecino and m.local_vecino.strip():
                    nombre = m.local_vecino.strip()
                    c = Cliente.query.filter(Cliente.nombre_o_razon_social.ilike(nombre)).first()
                    if not c:
                        c = Cliente(nombre_o_razon_social=nombre)
                        db.session.add(c)
                        db.session.flush()
                    m.cliente_id = c.id
            db.session.commit()
        except Exception as e:
            db.session.rollback()

        print("[OK] Base de datos actualizada y todas las columnas sincronizadas correctamente.")

if __name__ == '__main__':
    main()
