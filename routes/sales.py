from flask import Blueprint, request, jsonify, flash, redirect, render_template, abort, url_for
from flask_login import login_required, current_user
from models import db, Product, ProductVariant, Sale, SaleDetail, SalePayment, Expense, Maneo, Cliente, obtener_hora_bogota
from decorators import admin_required
from decimal import Decimal
from datetime import datetime, timedelta
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

sales_bp = Blueprint('sales_bp', __name__)

@sales_bp.route('/nueva', methods=['GET', 'POST'])
@login_required # Importante: Te bloqueará el acceso si no hay current_user logeado (Flask-Login)
def procesar_venta():
    if request.method == 'GET':
        return render_template('sales/nueva.html')

    """
    Se espera que los datos vengan en el cuerpo de la petición (JSON)
    Ej: {'items': [{ 'product_id': 1, 'cantidad': 2, 'precio_final': 15.50}, ...], 'metodo_pago': 'transferencia'}
    """
    data = request.get_json()
    items = data.get('items', [])
    pagos_data = data.get('pagos', [])  # Nuevo: array de pagos mixtos
    metodo_pago_legacy = data.get('metodo_pago', 'efectivo')  # Retrocompatibilidad
    
    if not items:
        return jsonify({'error': 'No se enviaron productos para la venta'}), 400

    # Si no se envían pagos en el nuevo formato, crear uno único con el método legacy
    if not pagos_data:
        pagos_data = [{'metodo_pago': metodo_pago_legacy, 'monto': None}]  # monto=None se llenará con el total

    try:
        # Determinar el método de pago principal (para la columna legacy de retrocompatibilidad)
        if len(pagos_data) == 1:
            metodo_pago_principal = pagos_data[0].get('metodo_pago', 'efectivo')
        else:
            metodo_pago_principal = 'mixto'

        # Manejar Fecha de Venta para registros de fechas anteriores
        fecha_venta_str = data.get('fecha_venta')
        fecha_venta_obj = obtener_hora_bogota()
        if fecha_venta_str:
            try:
                fecha_seleccionada = datetime.strptime(fecha_venta_str, '%Y-%m-%d').date()
                if fecha_seleccionada != fecha_venta_obj.date():
                    # Si no es hoy, combinamos la fecha seleccionada con la hora actual para conservar secuencialidad de hora de registro
                    fecha_venta_obj = datetime.combine(fecha_seleccionada, fecha_venta_obj.time())
            except ValueError:
                pass # Fallback silencioso a la hora actual si el formato falla

        nueva_venta = Sale(
            vendedor_id=current_user.id,
            monto_total=Decimal('0.00'),
            metodo_pago=metodo_pago_principal,
            fecha_venta=fecha_venta_obj
        )
        db.session.add(nueva_venta)
        db.session.flush()

        monto_total = Decimal('0.00')

        for item in items:
            product_id = item.get('product_id')
            variant_id = item.get('variant_id') # Posible variante
            cantidad_vendida = int(item.get('cantidad', 0))
            precio_venta_final = Decimal(str(item.get('precio_final', '0.00')))
            es_manual = item.get('es_manual', False)
            es_obsequio = item.get('es_obsequio', False)

            if cantidad_vendida <= 0:
                raise ValueError("La cantidad vendida debe ser mayor a 0.")

            if es_manual:
                # Producto manual (prestado de otro local) — no descuenta stock
                nombre_manual = item.get('nombre_manual', 'Producto Externo')
                precio_costo_manual = Decimal(str(item.get('precio_costo', '0.00')))

                detalle = SaleDetail(
                    sale_id=nueva_venta.id,
                    product_id=None,
                    variant_id=None,
                    cantidad_vendida=cantidad_vendida,
                    precio_venta_final=precio_venta_final,
                    nombre_manual=nombre_manual,
                    precio_costo_manual=precio_costo_manual
                )
                db.session.add(detalle)
                monto_total += (precio_venta_final * cantidad_vendida)

                # Crear el gasto automático para descontar el ingreso prestado del balance final
                if precio_costo_manual > 0:
                    gasto_externo = Expense(
                        usuario_id=current_user.id,
                        tipo_gasto='Gasto Diario',
                        categoria='Pago Prod. Externo',
                        descripcion=f"Pago por producto manual prestado: {nombre_manual}",
                        monto=(precio_costo_manual * cantidad_vendida),
                        fecha_gasto=fecha_venta_obj
                    )
                    db.session.add(gasto_externo)
            else:
                # Producto del inventario propio
                producto = Product.query.with_for_update().get(product_id)
                
                if not producto:
                    raise ValueError(f"El producto con ID {product_id} no existe.")

                if variant_id:
                    variante = ProductVariant.query.with_for_update().get(variant_id)
                    if not variante:
                        raise ValueError(f"La variante con ID {variant_id} no existe.")
                    if cantidad_vendida > variante.cantidad_stock:
                        raise ValueError(f"Stock insuficiente para la variante '{variante.nombre_variante}' de '{producto.nombre}'. Solicitado: {cantidad_vendida}, Disponible: {variante.cantidad_stock}.")
                    
                    stock_anterior = variante.cantidad_stock
                    variante.cantidad_stock -= cantidad_vendida
                    producto.cantidad_stock -= cantidad_vendida # Sincronizar producto base
                    precio_limite_autorizado = variante.precio_costo if current_user.rol == 'admin' else variante.precio_minimo
                    
                    from models import StockAdjustment
                    ajuste = StockAdjustment(
                        product_id=producto.id,
                        admin_id=current_user.id,
                        tipo_movimiento=f"Venta Tienda (Subcat: {variante.nombre_variante})",
                        stock_anterior=stock_anterior,
                        stock_nuevo=variante.cantidad_stock
                    )
                    db.session.add(ajuste)
                else:
                    if cantidad_vendida > producto.cantidad_stock:
                        raise ValueError(f"Stock insuficiente para el producto '{producto.nombre}'. Solicitado: {cantidad_vendida}, Disponible: {producto.cantidad_stock}.")
                    
                    stock_anterior = producto.cantidad_stock
                    producto.cantidad_stock -= cantidad_vendida
                    precio_limite_autorizado = producto.precio_costo if current_user.rol == 'admin' else producto.precio_minimo
                    
                    from models import StockAdjustment
                    ajuste = StockAdjustment(
                        product_id=producto.id,
                        admin_id=current_user.id,
                        tipo_movimiento="Venta Tienda",
                        stock_anterior=stock_anterior,
                        stock_nuevo=producto.cantidad_stock
                    )
                    db.session.add(ajuste)

                if not es_obsequio and precio_venta_final < precio_limite_autorizado:
                    raise ValueError(f"No autorizado: El precio ({precio_venta_final}) del producto '{producto.nombre}' está por debajo del límite permitido ({precio_limite_autorizado}).")

                detalle = SaleDetail(
                    sale_id=nueva_venta.id,
                    product_id=producto.id,
                    variant_id=variant_id,
                    cantidad_vendida=cantidad_vendida,
                    precio_venta_final=precio_venta_final
                )
                db.session.add(detalle)
                db.session.flush() # Importante para tener el id de la venta si se quisiera, pero ya lo tenemos en nueva_venta.id
                
                # Para añadir el ID de la venta al tipo de movimiento ahora que la venta tiene ID asignado:
                ajuste.tipo_movimiento = f"{ajuste.tipo_movimiento} #{nueva_venta.id}"
                
                monto_total += (precio_venta_final * cantidad_vendida)

        nueva_venta.monto_total = monto_total

        # Registrar los pagos mixtos en la tabla sale_payments
        total_pagos = Decimal('0.00')
        for pago_info in pagos_data:
            metodo = pago_info.get('metodo_pago', 'efectivo')
            monto_pago = pago_info.get('monto')
            
            if monto_pago is None:
                # Si solo hay un pago sin monto explícito, asignar el total completo
                monto_pago = monto_total
            else:
                monto_pago = Decimal(str(monto_pago))
            
            if monto_pago <= 0:
                raise ValueError(f"El monto del pago por '{metodo}' debe ser mayor a 0.")
            
            pago = SalePayment(
                sale_id=nueva_venta.id,
                metodo_pago=metodo,
                monto=monto_pago
            )
            db.session.add(pago)
            total_pagos += monto_pago

        # Validar que la suma de pagos cubra el total de la venta
        if total_pagos != monto_total:
            raise ValueError(f"La suma de los pagos (${total_pagos}) no coincide con el total de la venta (${monto_total}). Diferencia: ${monto_total - total_pagos}.")


        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': 'Venta registrada e inventario descontado con éxito.',
            'sale_id': nueva_venta.id,
            'total': str(monto_total)
        }), 201

    except ValueError as val_err:
        db.session.rollback()
        return jsonify({'error': str(val_err)}), 400
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Ocurrió un error interno al procesar la venta.'}), 500

@sales_bp.route('/api/search_products')
@login_required
def api_search_products():
    query = request.args.get('q', '').strip()
    
    if len(query) < 2:
        return jsonify([])
    
    productos = Product.query.filter_by(tipo_inventario='tienda').filter(
        or_(
            Product.sku.ilike(f'%{query}%'),
            Product.nombre.ilike(f'%{query}%')
        )
    ).limit(10).all()
    
    results = []
    for p in productos:
        # Preparar data de la misma manera que el endpoint de escaner exacto
        variantes_data = []
        if p.variantes:
            for v in p.variantes:
                variantes_data.append({
                    'id': v.id,
                    'nombre': v.nombre_variante,
                    'stock': v.cantidad_stock,
                    'precio_costo': float(v.precio_costo) if v.precio_costo else None,
                    'precio_minimo': float(v.precio_minimo) if v.precio_minimo else None,
                    'precio_sugerido': float(v.precio_sugerido) if v.precio_sugerido else None
                })
        
        results.append({
            'id': p.id,
            'nombre': p.nombre,
            'sku': p.sku,
            'tipo_inventario': p.tipo_inventario,
            'cantidad_stock': p.total_stock,
            'precio_minimo': float(p.precio_minimo),
            'precio_sugerido': float(p.precio_sugerido),
            'precio_costo': float(p.precio_costo),
            'variantes': variantes_data
        })
    
    return jsonify(results)

# Endpoint API asíncrono para el escáner del Punto de Venta
@sales_bp.route('/api/producto/<path:sku>', methods=['GET'])
@login_required
def api_buscar_producto(sku):
    producto = Product.query.filter(Product.sku == sku, Product.tipo_inventario == 'tienda').first()
    auto_select_variant = None
    
    if not producto:
        return jsonify({'error': 'Código SKU no encontrado en el sistema'}), 404
        
    return jsonify({
        'id': producto.id,
        'nombre': producto.nombre,
        'sku': producto.sku,
        'tipo_inventario': producto.tipo_inventario,
        'cantidad_stock': producto.total_stock,
        'precio_minimo': float(producto.precio_minimo),
        'precio_limite': float(producto.precio_costo) if current_user.rol == 'admin' else float(producto.precio_minimo),
        'precio_sugerido': float(producto.precio_sugerido),
        'variantes': [{"id": v.id, "nombre": v.nombre_variante, "stock": v.cantidad_stock, "precio_minimo": float(v.precio_minimo or producto.precio_minimo), "precio_limite": float(v.precio_costo or producto.precio_costo) if current_user.rol == 'admin' else float(v.precio_minimo or producto.precio_minimo), "precio_sugerido": float(v.precio_sugerido or producto.precio_sugerido)} for v in producto.variantes],
        'auto_select_variant': auto_select_variant
    })

# Ruta para la Impresión del formato Térmico (Ticket)
@sales_bp.route('/recibo/<int:sale_id>', methods=['GET'])
@login_required # Proteger confidencialidad del cajero
def imprimir_ticket(sale_id):
    # Regla: Retorna 404 si alguien ingresa un ID falso
    venta = Sale.query.get_or_404(sale_id)
    return render_template('sales/ticket.html', venta=venta)

# Endpoint Historial de Operaciones (Administradores: Unificado, Ventas y Maneos Cobrados)
@sales_bp.route('/historial', methods=['GET'])
@login_required
@admin_required
def historial():
    import calendar
    ahora_bogota = obtener_hora_bogota()
    hoy_bogota = ahora_bogota.strftime('%Y-%m-%d')
    mes_actual = ahora_bogota.month
    anio_actual = ahora_bogota.year

    tipo_operacion = request.args.get('tipo_operacion', 'todas').strip().lower()
    if tipo_operacion not in ['todas', 'ventas', 'maneos']:
        tipo_operacion = 'todas'

    tipo_filtro = request.args.get('tipo_filtro', '').strip()
    mes_param = request.args.get('mes')
    anio_param = request.args.get('anio')
    q_busqueda = request.args.get('q', '').strip()

    # Si no se envía tipo_filtro pero sí fecha_inicio / fecha_fin distintos a hoy
    fecha_inicio = request.args.get('fecha_inicio', '').strip()
    fecha_fin = request.args.get('fecha_fin', '').strip()

    if mes_param and not tipo_filtro:
        tipo_filtro = 'mes'
    elif fecha_inicio or fecha_fin:
        if not tipo_filtro:
            tipo_filtro = 'rango'
    elif not tipo_filtro:
        tipo_filtro = 'hoy'

    mes_sel = int(mes_param) if mes_param and mes_param.isdigit() else mes_actual
    anio_sel = int(anio_param) if anio_param and anio_param.isdigit() else anio_actual

    if tipo_filtro == 'hoy':
        inicio_dt = datetime.strptime(hoy_bogota, '%Y-%m-%d')
        fin_dt = inicio_dt + timedelta(days=1)
        fecha_inicio = hoy_bogota
        fecha_fin = hoy_bogota
    elif tipo_filtro == 'mes':
        _, last_day = calendar.monthrange(anio_sel, mes_sel)
        inicio_dt = datetime(anio_sel, mes_sel, 1, 0, 0, 0)
        fin_dt = datetime(anio_sel, mes_sel, last_day, 23, 59, 59)
        fecha_inicio = inicio_dt.strftime('%Y-%m-%d')
        fecha_fin = fin_dt.strftime('%Y-%m-%d')
    else: # rango
        if not fecha_inicio:
            fecha_inicio = hoy_bogota
        if not fecha_fin:
            fecha_fin = hoy_bogota
        
        try:
            inicio_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d')
            fin_dt = datetime.strptime(fecha_fin, '%Y-%m-%d') + timedelta(days=1)
        except ValueError:
            inicio_dt = datetime.strptime(hoy_bogota, '%Y-%m-%d')
            fin_dt = inicio_dt + timedelta(days=1)

    # 1. Consultar Ventas en el período
    query_v = Sale.query.options(
        joinedload(Sale.vendedor),
        joinedload(Sale.detalles).joinedload(SaleDetail.producto),
        joinedload(Sale.detalles).joinedload(SaleDetail.variante),
        joinedload(Sale.pagos)
    ).filter(Sale.fecha_venta >= inicio_dt, Sale.fecha_venta < fin_dt)

    if q_busqueda and tipo_operacion in ['todas', 'ventas']:
        clean_q = q_busqueda.replace('#', '').strip()
        if clean_q.isdigit():
            query_v = query_v.filter(Sale.id == int(clean_q))

    ventas_en_periodo = query_v.order_by(Sale.fecha_venta.desc()).all()

    # 2. Consultar Maneos Cobrados en el período
    query_m = Maneo.query.options(
        joinedload(Maneo.producto),
        joinedload(Maneo.variante),
        joinedload(Maneo.cliente)
    ).filter(
        Maneo.estado == 'FACTURADO',
        or_(
            (Maneo.fecha_resolucion >= inicio_dt) & (Maneo.fecha_resolucion < fin_dt),
            (Maneo.fecha_resolucion.is_(None)) & (Maneo.fecha_prestamo >= inicio_dt) & (Maneo.fecha_prestamo < fin_dt)
        )
    )

    if q_busqueda and tipo_operacion in ['todas', 'maneos']:
        clean_q = q_busqueda.replace('#', '').strip()
        if clean_q.isdigit():
            query_m = query_m.filter(or_(
                Maneo.id == int(clean_q),
                Maneo.local_vecino.ilike(f'%{q_busqueda}%'),
                Maneo.producto.has(Product.nombre.ilike(f'%{q_busqueda}%'))
            ))
        else:
            query_m = query_m.filter(or_(
                Maneo.local_vecino.ilike(f'%{q_busqueda}%'),
                Maneo.cliente.has(Cliente.nombre_o_razon_social.ilike(f'%{q_busqueda}%')),
                Maneo.producto.has(Product.nombre.ilike(f'%{q_busqueda}%'))
            ))

    maneos_en_periodo = query_m.order_by(Maneo.fecha_resolucion.desc(), Maneo.fecha_prestamo.desc()).all()

    # 3. Calcular Totales Específicos
    total_ventas_periodo = sum(v.monto_total for v in ventas_en_periodo)
    total_maneos_periodo = sum(Decimal(str(m.subtotal_calculado)) for m in maneos_en_periodo)
    gran_total_unificado = total_ventas_periodo + total_maneos_periodo

    count_ventas = len(ventas_en_periodo)
    count_maneos = len(maneos_en_periodo)
    count_todas = count_ventas + count_maneos

    # Totales de desglose financiero según tipo_operacion seleccionado
    total_efectivo = Decimal('0')
    total_nequi = Decimal('0')
    total_bancolombia = Decimal('0')
    total_daviplata = Decimal('0')
    total_transferencia_legacy = Decimal('0')
    total_mixto = 0
    total_general = Decimal('0')

    # Sumar Ventas si corresponde
    if tipo_operacion in ['todas', 'ventas']:
        for v in ventas_en_periodo:
            total_general += v.monto_total
            if v.pagos:
                for pago in v.pagos:
                    met = (pago.metodo_pago or 'efectivo').lower()
                    if met == 'efectivo':
                        total_efectivo += pago.monto
                    elif met == 'nequi':
                        total_nequi += pago.monto
                    elif met == 'bancolombia':
                        total_bancolombia += pago.monto
                    elif met == 'daviplata':
                        total_daviplata += pago.monto
                    elif met == 'transferencia':
                        total_transferencia_legacy += pago.monto
                if len(v.pagos) > 1:
                    total_mixto += 1
            else:
                met = (v.metodo_pago or 'efectivo').lower()
                if met == 'efectivo':
                    total_efectivo += v.monto_total
                elif met == 'nequi':
                    total_nequi += v.monto_total
                elif met == 'bancolombia':
                    total_bancolombia += v.monto_total
                elif met == 'daviplata':
                    total_daviplata += v.monto_total
                elif met == 'transferencia':
                    total_transferencia_legacy += v.monto_total

    # Sumar Maneos si corresponde
    if tipo_operacion in ['todas', 'maneos']:
        if tipo_operacion == 'maneos':
            total_general = Decimal('0') # Solo maneos
        for m in maneos_en_periodo:
            sub = Decimal(str(m.subtotal_calculado))
            if tipo_operacion == 'maneos':
                total_general += sub
            met = (m.metodo_pago or 'efectivo').lower()
            if met == 'efectivo':
                total_efectivo += sub
            elif met == 'nequi':
                total_nequi += sub
            elif met == 'bancolombia':
                total_bancolombia += sub
            elif met == 'daviplata':
                total_daviplata += sub
            else:
                total_efectivo += sub

    # Crear lista unificada de operaciones para la vista 'todas'
    operaciones_unificadas = []
    if tipo_operacion == 'todas':
        for v in ventas_en_periodo:
            operaciones_unificadas.append({
                'tipo': 'venta',
                'fecha': v.fecha_venta,
                'id': v.id,
                'objeto': v,
                'monto': v.monto_total
            })
        for m in maneos_en_periodo:
            operaciones_unificadas.append({
                'tipo': 'maneo',
                'fecha': m.fecha_resolucion or m.fecha_prestamo or ahora_bogota,
                'id': m.id,
                'objeto': m,
                'monto': Decimal(str(m.subtotal_calculado))
            })
        # Ordenar cronológicamente descendente
        operaciones_unificadas.sort(key=lambda x: x['fecha'], reverse=True)

    nombres_meses = {
        1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril', 5: 'Mayo', 6: 'Junio',
        7: 'Julio', 8: 'Agosto', 9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'
    }

    return render_template('sales/historial.html', 
                           tipo_operacion=tipo_operacion,
                           ventas=ventas_en_periodo, 
                           maneos=maneos_en_periodo,
                           operaciones_unificadas=operaciones_unificadas,
                           count_ventas=count_ventas,
                           count_maneos=count_maneos,
                           count_todas=count_todas,
                           total_ventas_periodo=total_ventas_periodo,
                           total_maneos_periodo=total_maneos_periodo,
                           gran_total_unificado=gran_total_unificado,
                           total_efectivo=total_efectivo,
                           total_nequi=total_nequi,
                           total_bancolombia=total_bancolombia,
                           total_daviplata=total_daviplata,
                           total_transferencia_legacy=total_transferencia_legacy,
                           total_mixto=total_mixto,
                           total_general=total_general,
                           fecha_inicio=fecha_inicio,
                           fecha_fin=fecha_fin,
                           tipo_filtro=tipo_filtro,
                           mes_sel=mes_sel,
                           anio_sel=anio_sel,
                           nombre_mes_sel=nombres_meses.get(mes_sel, ''),
                           q_busqueda=q_busqueda)


# Endpoint para Cambiar Vía / Método de Pago de Ventas o Maneos (Soporta Pago Único y Pago Dividido)
@sales_bp.route('/cambiar_metodo_pago', methods=['POST'])
@login_required
@admin_required
def cambiar_metodo_pago():
    tipo = request.form.get('tipo', 'venta').strip().lower()  # 'venta' o 'maneo'
    item_id = request.form.get('id')
    modalidad_pago = request.form.get('modalidad_pago', 'unico').strip().lower()  # 'unico' o 'dividido'
    redirect_url = request.form.get('redirect_url')

    try:
        if tipo == 'maneo':
            maneo = Maneo.query.get_or_404(item_id)
            total_operacion = Decimal(str(maneo.subtotal_calculado))
        else:
            venta = Sale.query.get_or_404(item_id)
            total_operacion = Decimal(str(venta.monto_total))

        if modalidad_pago == 'dividido':
            # Procesar Pago Dividido (Múltiples métodos)
            pagos_divididos = []
            monto_efectivo = Decimal(str(request.form.get('monto_efectivo', '0') or 0))
            monto_nequi = Decimal(str(request.form.get('monto_nequi', '0') or 0))
            monto_bancolombia = Decimal(str(request.form.get('monto_bancolombia', '0') or 0))
            monto_daviplata = Decimal(str(request.form.get('monto_daviplata', '0') or 0))

            if monto_efectivo > 0:
                pagos_divididos.append(('efectivo', monto_efectivo))
            if monto_nequi > 0:
                pagos_divididos.append(('nequi', monto_nequi))
            if monto_bancolombia > 0:
                pagos_divididos.append(('bancolombia', monto_bancolombia))
            if monto_daviplata > 0:
                pagos_divididos.append(('daviplata', monto_daviplata))

            suma_pagos = sum(m for _, m in pagos_divididos)
            if suma_pagos != total_operacion:
                flash(f'La suma del pago dividido (${suma_pagos:,.0f}) no coincide con el total (${total_operacion:,.0f}). Diferencia: ${abs(total_operacion - suma_pagos):,.0f}.', 'danger')
                return redirect(redirect_url or url_for('sales_bp.historial'))

            if tipo == 'maneo':
                maneo.metodo_pago = 'mixto' if len(pagos_divididos) > 1 else pagos_divididos[0][0]
                db.session.commit()
                flash(f'Pago del Maneo #{maneo.id:05d} actualizado a Pago Dividido (${total_operacion:,.0f}) exitosamente.', 'success')
            else:
                venta.metodo_pago = 'mixto' if len(pagos_divididos) > 1 else pagos_divididos[0][0]
                SalePayment.query.filter_by(sale_id=venta.id).delete()
                for met, monto in pagos_divididos:
                    pago_obj = SalePayment(
                        sale_id=venta.id,
                        metodo_pago=met,
                        monto=monto
                    )
                    db.session.add(pago_obj)
                db.session.commit()
                flash(f'Vía de pago del Ticket #{venta.id:05d} actualizada a Pago Dividido exitosamente.', 'success')

        else:
            # Procesar Pago Único
            nuevo_metodo = request.form.get('nuevo_metodo', 'efectivo').strip().lower()
            metodos_validos = ['efectivo', 'nequi', 'bancolombia', 'daviplata']
            if nuevo_metodo not in metodos_validos:
                flash(f'Método de pago no válido: {nuevo_metodo}', 'danger')
                return redirect(redirect_url or url_for('sales_bp.historial'))

            if tipo == 'maneo':
                maneo.metodo_pago = nuevo_metodo
                db.session.commit()
                flash(f'Vía de pago del Maneo #{maneo.id:05d} cambiada a {nuevo_metodo.capitalize()} exitosamente.', 'success')
            else:
                venta.metodo_pago = nuevo_metodo
                SalePayment.query.filter_by(sale_id=venta.id).delete()
                nuevo_pago = SalePayment(
                    sale_id=venta.id,
                    metodo_pago=nuevo_metodo,
                    monto=venta.monto_total
                )
                db.session.add(nuevo_pago)
                db.session.commit()
                flash(f'Vía de pago del Ticket #{venta.id:05d} cambiada a {nuevo_metodo.capitalize()} exitosamente.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error al cambiar el método de pago: {str(e)}', 'danger')

    if redirect_url:
        return redirect(redirect_url)
    return redirect(url_for('sales_bp.historial', tipo_operacion=('maneos' if tipo == 'maneo' else 'ventas')))


# Endpoint Visor de Ventas del Día para Cajeros (Solo lectura, se resetea cada día)
@sales_bp.route('/ventas_hoy', methods=['GET'])
@login_required
def ventas_hoy():
    # Obtener la fecha de hoy
    hoy_bogota = obtener_hora_bogota().date()
    # Para la consulta requerimos abarcar desde las 00:00:00 hasta las 23:59:59
    inicio_dt = datetime.combine(hoy_bogota, datetime.min.time())
    fin_dt = datetime.combine(hoy_bogota, datetime.max.time())
    
    # Consultar todas las ventas de este día (sin importar si es admin o vendedor)
    ventas = Sale.query.options(joinedload(Sale.vendedor)).filter(
        Sale.fecha_venta >= inicio_dt,
        Sale.fecha_venta <= fin_dt
    ).order_by(Sale.fecha_venta.desc()).all()
    
    # Acumuladores de las ventas de hoy
    total_efectivo = Decimal('0')
    total_transferencias = Decimal('0')
    total_mixto = 0
    
    for v in ventas:
        if v.pagos:
            for pago in v.pagos:
                if pago.metodo_pago == 'efectivo':
                    total_efectivo += pago.monto
                else: 
                    total_transferencias += pago.monto
            if len(v.pagos) > 1:
                total_mixto += 1
        else:
            if v.metodo_pago == 'efectivo':
                total_efectivo += v.monto_total
            else:
                total_transferencias += v.monto_total
                
    return render_template('sales/ventas_hoy.html',
                           ventas=ventas,
                           total_efectivo=total_efectivo,
                           total_transferencias=total_transferencias,
                           total_mixto=total_mixto,
                           hoy=hoy_bogota.strftime('%Y-%m-%d'))


# Endpoint para Anular/Eliminar Venta Histórica
@sales_bp.route('/eliminar/<int:sale_id>', methods=['POST'])
@login_required
@admin_required
def eliminar_venta(sale_id):
    venta = Sale.query.get_or_404(sale_id)
    
    try:
        # Revertir Stock
        from models import StockAdjustment
        for detalle in venta.detalles:
            if detalle.variant_id:
                variante = ProductVariant.query.with_for_update().get(detalle.variant_id)
                if variante:
                    stock_anterior = variante.cantidad_stock
                    variante.cantidad_stock += detalle.cantidad_vendida
                    
                    ajuste = StockAdjustment(
                        product_id=detalle.product_id,
                        admin_id=current_user.id,
                        tipo_movimiento=f"Anulación Venta #{venta.id} (Subcat: {variante.nombre_variante})",
                        stock_anterior=stock_anterior,
                        stock_nuevo=variante.cantidad_stock
                    )
                    db.session.add(ajuste)
                    
                producto = Product.query.with_for_update().get(detalle.product_id)
                if producto:
                    producto.cantidad_stock += detalle.cantidad_vendida
            elif detalle.product_id:
                producto = Product.query.with_for_update().get(detalle.product_id)
                if producto:
                    stock_anterior = producto.cantidad_stock
                    producto.cantidad_stock += detalle.cantidad_vendida
                    
                    ajuste = StockAdjustment(
                        product_id=producto.id,
                        admin_id=current_user.id,
                        tipo_movimiento=f"Anulación Venta #{venta.id}",
                        stock_anterior=stock_anterior,
                        stock_nuevo=producto.cantidad_stock
                    )
                    db.session.add(ajuste)
                    
        # Eliminar Venta y Detalles (Cascada)
        db.session.delete(venta)
        db.session.commit()
        flash('Venta anulada y stock devuelto exitosamente.', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash('Ocurrió un error al anular la venta.', 'danger')
        
    return redirect(url_for('sales_bp.historial'))

# Endpoint Catálogo Estricto de solo vista para Operarios
@sales_bp.route('/catalogo', methods=['GET'])
@login_required 
def catalogo():
    query_str = request.args.get('q', '').strip()
    
    if query_str:
        # Motor de similitud Case-Insensitive (Like)
        search_term = f"%{query_str}%"
        productos = Product.query.filter(Product.tipo_inventario == 'tienda').filter(
            or_(
                Product.sku.ilike(search_term), 
                Product.nombre.ilike(search_term)
            )
        ).limit(50).all()
    else:
        # Límite pasivo de 50 ítems para ahorrar memoria RAM de BD en carga inicial
        productos = Product.query.filter(Product.tipo_inventario == 'tienda').limit(50).all()
        
    return render_template('sales/catalogo.html', productos=productos, q=query_str)

@sales_bp.route('/caja_visual', methods=['GET'])
@login_required
def caja_visual():
    from models import obtener_hora_bogota
    hoy_bogota = obtener_hora_bogota()
    productos = Product.query.options(db.selectinload(Product.variantes)).filter(Product.tipo_inventario == 'tienda').order_by(Product.nombre.asc()).all()
    return render_template('sales/caja_visual.html', productos=productos, hoy=hoy_bogota.strftime('%Y-%m-%d'))

