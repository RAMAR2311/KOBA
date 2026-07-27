import os
from app import create_app
from models import db, User, Product, Cliente, FacturaBodega, FacturaBodegaDetalle, AbonoBodega
from decimal import Decimal

def seed_bodega():
    app = create_app()
    with app.app_context():
        # 1. Obtener Usuario de Bodega
        bodega_user = User.query.filter_by(rol='bodega').first()
        if not bodega_user:
            print("❌ Error: No se encontró el usuario de bodega en la base de datos.")
            return

        # Asegurar carpetas para archivos de facturas
        os.makedirs('static/uploads/facturas', exist_ok=True)

        # 2. Crear Productos exclusivos de Bodega
        products_data = [
            {
                "nombre": "iPhone 15 Pro Max 256GB - Lote Bodega", 
                "sku": "BOD-I15PM-256", 
                "cantidad_stock": 50, 
                "precio_costo": 4500000, 
                "precio_minimo": 4800000, 
                "precio_sugerido": 5200000
            },
            {
                "nombre": "Samsung Galaxy S24 Ultra - Lote Bodega", 
                "sku": "BOD-S24U-512", 
                "cantidad_stock": 30, 
                "precio_costo": 3800000, 
                "precio_minimo": 4100000, 
                "precio_sugerido": 4500000
            },
            {
                "nombre": "AirPods Pro 2nd Gen - Bodega", 
                "sku": "BOD-APP2", 
                "cantidad_stock": 100, 
                "precio_costo": 700000, 
                "precio_minimo": 850000, 
                "precio_sugerido": 950000
            },
            {
                "nombre": "Cargador Apple 20W Original - Pack 10", 
                "sku": "BOD-CHG-20W", 
                "cantidad_stock": 200, 
                "precio_costo": 80000, 
                "precio_minimo": 110000, 
                "precio_sugerido": 135000
            }
        ]

        for p_data in products_data:
            existing = Product.query.filter_by(sku=p_data['sku']).first()
            if not existing:
                p = Product(
                    nombre=p_data['nombre'],
                    sku=p_data['sku'],
                    tipo_inventario='bodega',
                    cantidad_stock=p_data['cantidad_stock'],
                    precio_costo=p_data['precio_costo'],
                    precio_minimo=p_data['precio_minimo'],
                    precio_sugerido=p_data['precio_sugerido']
                )
                db.session.add(p)
            else:
                # Actualizar stock si ya existe
                existing.tipo_inventario = 'bodega'
                existing.cantidad_stock = p_data['cantidad_stock']
        
        db.session.commit()
        print("[INFO] Productos de bodega creados/actualizados.")

        # 3. Crear Clientes de Bodega (Mayoristas)
        clientes_mayoristas = [
            {
                "nombre_o_razon_social": "Distribuidora Tech Medellín SAS",
                "documento_o_nit": "900.888.777-1",
                "telefono": "6045551234",
                "email": "compras@techmedellin.com",
                "direccion": "Centro Comercial Monterrey, Local 123"
            },
            {
                "nombre_o_razon_social": "Inversiones Marlo Cali",
                "documento_o_nit": "800.111.222-3",
                "telefono": "3159998877",
                "email": "marlo@koba.com",
                "direccion": "Av. Pasoancho # 50-10"
            }
        ]

        for c_data in clientes_mayoristas:
            if not Cliente.query.filter_by(documento_o_nit=c_data['documento_o_nit']).first():
                cliente = Cliente(**c_data)
                db.session.add(cliente)
        
        db.session.commit()
        print("[INFO] Clientes mayoristas creados.")

        # 4. Crear una Factura de Bodega de ejemplo
        cliente_test = Cliente.query.filter_by(documento_o_nit="900.888.777-1").first()
        if cliente_test and not FacturaBodega.query.filter_by(numero_factura="FB-TEST-001").first():
            factura = FacturaBodega(
                cliente_id=cliente_test.id,
                usuario_id=bodega_user.id,
                numero_factura="FB-TEST-001",
                archivo_ruta="static/uploads/facturas/fb_test_001.pdf", # Ruta ficticia
                monto_total=Decimal('12400000.00'),
                estado='Pendiente'
            )
            db.session.add(factura)
            db.session.flush()

            # Agregar Detalles a la factura
            p1 = Product.query.filter_by(sku="BOD-I15PM-256").first()
            p2 = Product.query.filter_by(sku="BOD-CHG-20W").first()

            if p1:
                db.session.add(FacturaBodegaDetalle(
                    factura_id=factura.id,
                    producto_id=p1.id,
                    cantidad=2,
                    precio_venta=Decimal('5500000.00')
                ))
            
            if p2:
                db.session.add(FacturaBodegaDetalle(
                    factura_id=factura.id,
                    producto_id=p2.id,
                    cantidad=10,
                    precio_venta=Decimal('140000.00')
                ))

            # Registrar un abono inicial
            abono = AbonoBodega(
                cliente_id=cliente_test.id,
                factura_id=factura.id,
                usuario_id=bodega_user.id,
                monto=Decimal('4000000.00'),
                metodo_pago='transferencia',
                observacion='Abono inicial de prueba'
            )
            db.session.add(abono)
            factura.estado = 'Parcial'

            db.session.commit()
            print("[INFO] Factura de ejemplo FB-TEST-001 generada con abono parcial.")

        # 5. Crear otra Factura de Bodega (Contado / Pagada)
        cliente_test_2 = Cliente.query.filter_by(documento_o_nit="800.111.222-3").first()
        if cliente_test_2 and not FacturaBodega.query.filter_by(numero_factura="FB-TEST-002").first():
            factura2 = FacturaBodega(
                cliente_id=cliente_test_2.id,
                usuario_id=bodega_user.id,
                numero_factura="FB-TEST-002",
                archivo_ruta=None,
                monto_total=Decimal('2850000.00'),
                estado='Pagado',
                modalidad='contado'
            )
            db.session.add(factura2)
            db.session.flush()

            p3 = Product.query.filter_by(sku="BOD-APP2").first()
            if p3:
                db.session.add(FacturaBodegaDetalle(
                    factura_id=factura2.id,
                    producto_id=p3.id,
                    cantidad=3,
                    precio_venta=Decimal('950000.00')
                ))
            
            abono2 = AbonoBodega(
                cliente_id=cliente_test_2.id,
                factura_id=factura2.id,
                usuario_id=bodega_user.id,
                monto=Decimal('2850000.00'),
                metodo_pago='nequi',
                observacion='Pago completo de contado'
            )
            db.session.add(abono2)
            db.session.commit()
            print("[INFO] Factura de ejemplo FB-TEST-002 generada y pagada.")

if __name__ == '__main__':
    seed_bodega()
