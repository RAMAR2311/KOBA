from app import create_app
from models import db, Product, ProductVariant, StockAdjustment, User
import random
import string

def random_sku():
    return "TEST-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def create_more_sample_products():
    app = create_app()
    with app.app_context():
        # Get an admin user for the stock adjustments
        admin = User.query.filter_by(rol='admin').first()
        if not admin:
            admin = User.query.first()
            
        admin_id = admin.id if admin else 1

        print("Creating more sample products...")

        # 1. Product without variants
        prod1 = Product(
            sku=random_sku(),
            nombre="Cargador Rápido 20W PD",
            tipo_inventario="tienda",
            cantidad_stock=100,
            precio_costo=15000,
            precio_minimo=25000,
            precio_sugerido=35000,
            observacion="Cargador Tipo-C a Tipo-C."
        )
        db.session.add(prod1)
        db.session.flush()

        adj1 = StockAdjustment(
            product_id=prod1.id,
            admin_id=admin_id,
            tipo_movimiento='Creación Inicial (Ejemplo Extra)',
            stock_anterior=0,
            stock_nuevo=100
        )
        db.session.add(adj1)

        # 2. Product with variants (Red Cover)
        prod2 = Product(
            sku=random_sku(),
            nombre="Funda de Silicona Premium iPhone",
            tipo_inventario="tienda",
            cantidad_stock=0, # Base stock is 0 when there are variants
            precio_costo=12000,
            precio_minimo=22000,
            precio_sugerido=30000,
            observacion="Fundas variadas para modelos iPhone."
        )
        db.session.add(prod2)
        db.session.flush()

        variantes_prod2 = [
            ("iPhone 13 - Negro", 20, 12000, 22000, 30000),
            ("iPhone 13 - Rosa", 15, 12000, 22000, 30000),
            ("iPhone 14 Pro - Transparente", 30, 15000, 25000, 35000),
            ("iPhone 15 - Cuero Cafe", 10, 25000, 45000, 60000)
        ]

        for nombre, stock, costo, min_p, sug_p in variantes_prod2:
            v = ProductVariant(
                product_id=prod2.id,
                nombre_variante=nombre,
                cantidad_stock=stock,
                precio_costo=costo,
                precio_minimo=min_p,
                precio_sugerido=sug_p
            )
            db.session.add(v)
            
            adj = StockAdjustment(
                product_id=prod2.id,
                admin_id=admin_id,
                tipo_movimiento=f'Creación Subcategoría: {nombre}',
                stock_anterior=0,
                stock_nuevo=stock
            )
            db.session.add(adj)

        db.session.commit()
        print("More sample products with variants created successfully!")

if __name__ == '__main__':
    create_more_sample_products()
