from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, Product, ProductVariant, Maneo, StockAdjustment, Sale, SaleDetail, SalePayment, obtener_hora_bogota
from decorators import admin_required

maneos_bp = Blueprint('maneos_bp', __name__)

@maneos_bp.route('/')
@login_required
@admin_required
def index():
    # Listar todos los maneos activos (PENDIENTES) y un historial reciente de cerrados
    activos = Maneo.query.filter_by(estado='PENDIENTE').order_by(Maneo.fecha_prestamo.desc()).all()
    historial = Maneo.query.filter(Maneo.estado != 'PENDIENTE').order_by(Maneo.fecha_resolucion.desc()).limit(50).all()
    return render_template('maneos/index.html', activos=activos, historial=historial)

@maneos_bp.route('/prestar', methods=['POST'])
@login_required
@admin_required
def prestar():
    sku_busqueda = request.form.get('sku_busqueda', '').strip()
    local_vecino = request.form.get('local_vecino', '').strip()
    cantidad = int(request.form.get('cantidad', 1))

    if not sku_busqueda or not local_vecino or cantidad < 1:
        flash("Todos los campos son obligatorios y la cantidad debe ser mayor a 0.", "danger")
        return redirect(url_for('maneos_bp.index'))

    # Buscar el producto o variante por SKU
    producto = Product.query.filter_by(sku=sku_busqueda).first()
    variant_id = request.form.get('variant_id')
    variante = None

    if not producto:
        flash(f"No se encontró ningún producto con el SKU: {sku_busqueda}", "warning")
        return redirect(url_for('maneos_bp.index'))

    if producto.variantes:
        if not variant_id:
            flash(f"El producto '{producto.nombre}' tiene subcategorías. Debes especificar cuál vas a prestar.", "warning")
            return redirect(url_for('maneos_bp.index'))
            
        variante = ProductVariant.query.get(variant_id)
        if not variante or variante.product_id != producto.id:
            flash("Variante inválida.", "danger")
            return redirect(url_for('maneos_bp.index'))
            
        if variante.cantidad_stock < cantidad:
            flash(f"Stock insuficiente. Solo hay {variante.cantidad_stock} unidades disponibles de esta subcategoría.", "danger")
            return redirect(url_for('maneos_bp.index'))
            
        # Descontar Inventario Variante
        stock_anterior = variante.cantidad_stock
        variante.cantidad_stock -= cantidad
        producto.cantidad_stock -= cantidad # Reflejar en base
        
        # Registrar Ajuste
        from models import StockAdjustment
        ajuste = StockAdjustment(
            product_id=producto.id,
            admin_id=current_user.id,
            tipo_movimiento=f"Préstamo (Maneo) a {local_vecino} (Subcat: {variante.nombre_variante})",
            stock_anterior=stock_anterior,
            stock_nuevo=variante.cantidad_stock
        )
        db.session.add(ajuste)
    else:
        if producto.cantidad_stock < cantidad:
            flash(f"Stock insuficiente. Solo hay {producto.cantidad_stock} unidades disponibles.", "danger")
            return redirect(url_for('maneos_bp.index'))
            
        # Descontar Inventario Base
        stock_anterior = producto.cantidad_stock
        producto.cantidad_stock -= cantidad
        
        # Registrar Ajuste
        from models import StockAdjustment
        ajuste = StockAdjustment(
            product_id=producto.id,
            admin_id=current_user.id,
            tipo_movimiento=f"Préstamo (Maneo) a {local_vecino}",
            stock_anterior=stock_anterior,
            stock_nuevo=producto.cantidad_stock
        )
        db.session.add(ajuste)

    # 1. Crear el Maneo
    nuevo_maneo = Maneo(
        product_id=producto.id,
        variant_id=variante.id if variante else None,
        local_vecino=local_vecino,
        cantidad=cantidad,
        estado='PENDIENTE'
    )
    db.session.add(nuevo_maneo)

    try:
        db.session.commit()
        flash(f"Préstamo registrado exitosamente para {local_vecino}.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error al registrar el préstamo: {e}", "danger")

    return redirect(url_for('maneos_bp.index'))

@maneos_bp.route('/facturar/<int:id>', methods=['POST'])
@login_required
@admin_required
def facturar(id):
    maneo = Maneo.query.get_or_404(id)
    if maneo.estado != 'PENDIENTE':
        flash("Este maneo ya fue resuelto.", "warning")
        return redirect(url_for('maneos_bp.index'))

    # Opciones de facturación (puede venir del form)
    sugerido = maneo.variante.precio_sugerido if maneo.variant_id and maneo.variante else maneo.producto.precio_sugerido
    precio_unidad = float(request.form.get('precio_unidad', sugerido))
    metodo_pago = request.form.get('metodo_pago', 'efectivo')
    
    total_venta = precio_unidad * maneo.cantidad

    # Crear la Venta
    nueva_venta = Sale(
        vendedor_id=current_user.id,
        monto_total=total_venta,
        metodo_pago=metodo_pago
    )
    db.session.add(nueva_venta)
    db.session.flush()

    # Añadir detalle
    detalle = SaleDetail(
        sale_id=nueva_venta.id,
        product_id=maneo.product_id,
        variant_id=maneo.variant_id,
        cantidad_vendida=maneo.cantidad,
        precio_venta_final=precio_unidad
    )
    db.session.add(detalle)

    # Añadir pago (SalePayment)
    pago = SalePayment(
        sale_id=nueva_venta.id,
        metodo_pago=metodo_pago,
        monto=total_venta
    )
    db.session.add(pago)

    # Actualizar estado del Maneo
    maneo.estado = 'FACTURADO'
    maneo.metodo_pago = metodo_pago
    maneo.fecha_resolucion = obtener_hora_bogota()

    try:
        db.session.commit()
        flash(f"Maneo facturado correctamente. Venta registrada por ${total_venta:,.0f}.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error al facturar el maneo: {e}", "danger")

    return redirect(url_for('maneos_bp.index'))

@maneos_bp.route('/devolver/<int:id>', methods=['POST'])
@login_required
@admin_required
def devolver(id):
    maneo = Maneo.query.get_or_404(id)
    if maneo.estado != 'PENDIENTE':
        flash("Este maneo ya fue resuelto.", "warning")
        return redirect(url_for('maneos_bp.index'))

    # 1. Actualizar estado
    maneo.estado = 'DEVUELTO'
    maneo.fecha_resolucion = obtener_hora_bogota()

    # 2. Devolver stock
    producto = maneo.producto
    from models import StockAdjustment
    
    if maneo.variant_id:
        variante = ProductVariant.query.with_for_update().get(maneo.variant_id)
        if variante:
            stock_anterior = variante.cantidad_stock
            variante.cantidad_stock += maneo.cantidad
            producto.cantidad_stock += maneo.cantidad # Reflejar en base
            
            ajuste = StockAdjustment(
                product_id=producto.id,
                admin_id=current_user.id,
                tipo_movimiento=f"Devolución de Maneo de {maneo.local_vecino} (Subcat: {variante.nombre_variante})",
                stock_anterior=stock_anterior,
                stock_nuevo=variante.cantidad_stock
            )
            db.session.add(ajuste)
    else:
        stock_anterior = producto.cantidad_stock
        producto.cantidad_stock += maneo.cantidad
    
        ajuste = StockAdjustment(
            product_id=producto.id,
            admin_id=current_user.id,
            tipo_movimiento=f"Devolución de Maneo de {maneo.local_vecino}",
            stock_anterior=stock_anterior,
            stock_nuevo=producto.cantidad_stock
        )
        db.session.add(ajuste)

    try:
        db.session.commit()
        flash(f"Maneo devuelto. {maneo.cantidad} unidades regresaron al stock.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error al devolver el maneo: {e}", "danger")

    return redirect(url_for('maneos_bp.index'))
