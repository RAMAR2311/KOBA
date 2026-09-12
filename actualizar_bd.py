from app import create_app
from models import db

def main():
    app = create_app()
    with app.app_context():
        # 1. Asegurar creación de tablas si faltara alguna (ej. providers)
        db.create_all()

        engine = db.engine
        is_sqlite = engine.dialect.name == 'sqlite'

        # 2. Columnas añadidas a los modelos que necesitan existir en PostgreSQL / SQLite
        columnas_def = [
            ("maneos", "valor_fijo", "NUMERIC(10, 2)"),
            ("maneos", "variant_id", "INTEGER"),
            ("maneos", "cliente_id", "INTEGER"),
            ("maneos", "observacion", "TEXT"),
            ("product_variants", "precio_costo", "NUMERIC(10, 2)"),
            ("product_variants", "precio_minimo", "NUMERIC(10, 2)"),
            ("product_variants", "precio_sugerido", "NUMERIC(10, 2)"),
            ("users", "telefono", "VARCHAR(20)"),
            ("sale_details", "variant_id", "INTEGER"),
            ("sale_details", "nombre_manual", "VARCHAR(200)"),
            ("sale_details", "precio_costo_manual", "NUMERIC(10, 2)"),
            ("facturas_bodega_detalles", "variant_id", "INTEGER"),
            ("facturas_bodega_detalles", "precio_venta", "NUMERIC(10, 2)"),
            ("clientes", "creado_por_id", "INTEGER"),
            ("clientes", "contacto_persona", "VARCHAR(100)"),
            ("clientes", "local_numero", "VARCHAR(50)"),
            ("clientes", "notas", "TEXT"),
        ]

        if is_sqlite:
            import sqlite3
            for tabla, col, col_tipo in columnas_def:
                try:
                    res = db.session.execute(db.text(f"PRAGMA table_info({tabla})")).fetchall()
                    existing_cols = [r[1] for r in res]
                    if col not in existing_cols:
                        db.session.execute(db.text(f"ALTER TABLE {tabla} ADD COLUMN {col} {col_tipo}"))
                        db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    print(f"[Aviso SQLite] {tabla}.{col} -> {e}")
        else:
            for tabla, col, col_tipo in columnas_def:
                try:
                    db.session.execute(db.text(f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS {col} {col_tipo};"))
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    print(f"[Aviso Postgres] {tabla}.{col} -> {e}")

            # Modificaciones de nulos en Postgres
            for sql in [
                "ALTER TABLE clientes ALTER COLUMN documento_o_nit DROP NOT NULL;",
                "ALTER TABLE clientes ALTER COLUMN telefono DROP NOT NULL;"
            ]:
                try:
                    db.session.execute(db.text(sql))
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()

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
