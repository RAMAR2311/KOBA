from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models import db, Cliente, Maneo, Product, ProductVariant, Sale, SaleDetail, SalePayment, AbonoCliente, obtener_hora_bogota
from decimal import Decimal

clientes_bp = Blueprint('clientes_bp', __name__)

@clientes_bp.route('/', methods=['GET'])
@login_required
def index():
    q = request.args.get('q', '').strip()
    filtro = request.args.get('filtro', 'todos').strip()

    query = Cliente.query
    if q:
        from sqlalchemy import or_
        query = query.filter(
            or_(
                Cliente.nombre_o_razon_social.ilike(f'%{q}%'),
                Cliente.telefono.ilike(f'%{q}%'),
                Cliente.contacto_persona.ilike(f'%{q}%'),
                Cliente.local_numero.ilike(f'%{q}%'),
                Cliente.documento_o_nit.ilike(f'%{q}%')
            )
        )

    todos_los_clientes = Cliente.query.all()
    # Calcular KPIs globales del módulo
    total_clientes = len(todos_los_clientes)
    total_saldo_calle = sum(c.saldo_maneos_pendiente for c in todos_los_clientes)
    total_unidades_calle = sum(c.unidades_maneos_pendientes for c in todos_los_clientes)
    clientes_con_deuda_count = sum(1 for c in todos_los_clientes if c.saldo_maneos_pendiente > 0)

    lista_clientes = query.order_by(Cliente.nombre_o_razon_social.asc()).all()

    if filtro == 'con_deuda':
        lista_clientes = [c for c in lista_clientes if c.saldo_maneos_pendiente > 0]
    elif filtro == 'al_dia':
        lista_clientes = [c for c in lista_clientes if c.saldo_maneos_pendiente == 0]

    return render_template(
        'clientes/index.html',
        clientes=lista_clientes,
        total_clientes=total_clientes,
        total_saldo_calle=total_saldo_calle,
        total_unidades_calle=total_unidades_calle,
        clientes_con_deuda_count=clientes_con_deuda_count,
        q=q,
        filtro=filtro
    )

@clientes_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo():
    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        contacto_persona = request.form.get('contacto_persona', '').strip()
        local_numero = request.form.get('local_numero', '').strip()
        telefono = request.form.get('telefono', '').strip()
        email = request.form.get('email', '').strip()
        direccion = request.form.get('direccion', '').strip()
        documento_o_nit = request.form.get('documento_o_nit', '').strip() or None
        notas = request.form.get('notas', '').strip()

        if not nombre:
            flash('El nombre del local o persona es obligatorio.', 'danger')
            return render_template('clientes/form.html', cliente=None)

        if documento_o_nit:
            existente = Cliente.query.filter_by(documento_o_nit=documento_o_nit).first()
            if existente:
                flash(f'Ya existe un cliente con el documento/NIT "{documento_o_nit}".', 'warning')
                return render_template('clientes/form.html', cliente=None)

        nuevo_cliente = Cliente(
            nombre_o_razon_social=nombre,
            contacto_persona=contacto_persona,
            local_numero=local_numero,
            telefono=telefono,
            email=email,
            direccion=direccion,
            documento_o_nit=documento_o_nit,
            notas=notas,
            creado_por_id=current_user.id
        )

        try:
            db.session.add(nuevo_cliente)
            db.session.commit()
            flash(f'Cliente/Local "{nombre}" registrado exitosamente.', 'success')
            return redirect(url_for('clientes_bp.estado_cuenta', id=nuevo_cliente.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al guardar el cliente: {str(e)}', 'danger')

    return render_template('clientes/form.html', cliente=None)

@clientes_bp.route('/api/crear_rapido', methods=['POST'])
@login_required
def api_crear_rapido():
    """Endpoint AJAX para crear un local o persona rápidamente desde la vista de Maneos."""
    data = request.get_json(silent=True) or request.form
    nombre = (data.get('nombre') or '').strip()
    local_numero = (data.get('local_numero') or '').strip()
    contacto_persona = (data.get('contacto_persona') or '').strip()
    telefono = (data.get('telefono') or '').strip()

    if not nombre:
        return jsonify({'success': False, 'message': 'El nombre del local o persona es obligatorio.'}), 400

    # Buscar si ya existe uno con ese nombre para evitar duplicados accidentales
    existente = Cliente.query.filter(Cliente.nombre_o_razon_social.ilike(nombre)).first()
    if existente:
        return jsonify({
            'success': True,
            'cliente': {
                'id': existente.id,
                'nombre': existente.nombre_o_razon_social,
                'local': existente.local_numero or '',
                'saldo': existente.saldo_maneos_pendiente
            },
            'ya_existia': True
        })

    nuevo_c = Cliente(
        nombre_o_razon_social=nombre,
        local_numero=local_numero,
        contacto_persona=contacto_persona,
        telefono=telefono,
        creado_por_id=current_user.id
    )

    try:
        db.session.add(nuevo_c)
        db.session.commit()
        return jsonify({
            'success': True,
            'cliente': {
                'id': nuevo_c.id,
                'nombre': nuevo_c.nombre_o_razon_social,
                'local': nuevo_c.local_numero or '',
                'saldo': 0
            },
            'ya_existia': False
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error en base de datos: {str(e)}'}), 500

@clientes_bp.route('/<int:id>/estado_cuenta', methods=['GET'])
@login_required
def estado_cuenta(id):
    cliente = Cliente.query.get_or_404(id)

    # Ordenar maneos
    maneos_ordenados = sorted(cliente.maneos, key=lambda m: m.fecha_prestamo or obtener_hora_bogota(), reverse=True)
    maneos_activos = [m for m in maneos_ordenados if m.estado == 'PENDIENTE']
    maneos_historial = [m for m in maneos_ordenados if m.estado != 'PENDIENTE']

    saldo_pendiente = cliente.saldo_maneos_pendiente
    unidades_pendientes = cliente.unidades_maneos_pendientes
    total_prestado = cliente.total_historico_prestado
    total_cobrado = cliente.total_historico_cobrado
    total_devuelto = cliente.total_historico_devuelto

    return render_template(
        'clientes/estado_cuenta.html',
        cliente=cliente,
        maneos_activos=maneos_activos,
        maneos_historial=maneos_historial,
        saldo_pendiente=saldo_pendiente,
        unidades_pendientes=unidades_pendientes,
        total_prestado=total_prestado,
        total_cobrado=total_cobrado,
        total_devuelto=total_devuelto
    )

@clientes_bp.route('/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar(id):
    cliente = Cliente.query.get_or_404(id)

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        contacto_persona = request.form.get('contacto_persona', '').strip()
        local_numero = request.form.get('local_numero', '').strip()
        telefono = request.form.get('telefono', '').strip()
        email = request.form.get('email', '').strip()
        direccion = request.form.get('direccion', '').strip()
        documento_o_nit = request.form.get('documento_o_nit', '').strip() or None
        notas = request.form.get('notas', '').strip()

        if not nombre:
            flash('El nombre del local o persona es obligatorio.', 'danger')
            return render_template('clientes/form.html', cliente=cliente)

        if documento_o_nit and documento_o_nit != cliente.documento_o_nit:
            existente = Cliente.query.filter_by(documento_o_nit=documento_o_nit).first()
            if existente:
                flash(f'Ya existe otro cliente con el documento/NIT "{documento_o_nit}".', 'warning')
                return render_template('clientes/form.html', cliente=cliente)

        cliente.nombre_o_razon_social = nombre
        cliente.contacto_persona = contacto_persona
        cliente.local_numero = local_numero
        cliente.telefono = telefono
        cliente.email = email
        cliente.direccion = direccion
        cliente.documento_o_nit = documento_o_nit
        cliente.notas = notas

        try:
            db.session.commit()
            flash(f'Datos del cliente "{nombre}" actualizados correctamente.', 'success')
            return redirect(url_for('clientes_bp.estado_cuenta', id=cliente.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al actualizar el cliente: {str(e)}', 'danger')

    return render_template('clientes/form.html', cliente=cliente)

@clientes_bp.route('/<int:id>/eliminar', methods=['POST'])
@login_required
def eliminar(id):
    cliente = Cliente.query.get_or_404(id)

    if cliente.saldo_maneos_pendiente > 0 or len(cliente.maneos_activos) > 0:
        flash(f'No es posible eliminar a "{cliente.nombre_o_razon_social}" porque tiene {len(cliente.maneos_activos)} maneos pendientes por resolver (Saldo: ${cliente.saldo_maneos_pendiente:,.0f}).', 'danger')
        return redirect(url_for('clientes_bp.index'))

    try:
        # Desvincular maneos históricos para no romper integridad
        for m in cliente.maneos:
            m.cliente_id = None
        db.session.delete(cliente)
        db.session.commit()
        flash('Cliente / Local eliminado exitosamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar cliente: {str(e)}', 'danger')

    return redirect(url_for('clientes_bp.index'))
    
def _procesar_pagos_venta(tipo_pago, total_a_cobrar, form_data):
    """
    Parsea y valida los pagos únicos o divididos para una liquidación de maneos.
    Retorna (metodo_pago_principal, lista_de_pagos_dict, error_msg).
    """
    from decimal import Decimal
    total_decimal = Decimal(str(total_a_cobrar))
    
    if tipo_pago == 'dividido':
        metodo_1 = form_data.get('metodo_pago_1', 'efectivo').strip()
        monto_1_str = form_data.get('monto_1', '0').replace('$', '').replace('.', '').replace(',', '').strip() or '0'
        metodo_2 = form_data.get('metodo_pago_2', 'nequi').strip()
        monto_2_str = form_data.get('monto_2', '0').replace('$', '').replace('.', '').replace(',', '').strip() or '0'
        
        try:
            monto_1 = Decimal(monto_1_str)
            monto_2 = Decimal(monto_2_str)
        except Exception:
            return None, None, "Los montos ingresados para el pago dividido no son válidos."
            
        if monto_1 <= 0 or monto_2 <= 0:
            return None, None, "En pago dividido, ambos métodos deben tener un monto mayor a $0."
            
        if abs((monto_1 + monto_2) - total_decimal) > Decimal('1.00'):
            return None, None, f"La suma de los pagos (${(monto_1+monto_2):,.0f}) no coincide con el total a cobrar (${total_decimal:,.0f})."
            
        if (monto_1 + monto_2) != total_decimal:
            monto_2 = total_decimal - monto_1
            
        metodo_principal = f"{metodo_1}+{metodo_2}"
        pagos = [
            {'metodo': metodo_1, 'monto': monto_1},
            {'metodo': metodo_2, 'monto': monto_2}
        ]
        return metodo_principal, pagos, None
    else:
        metodo = form_data.get('metodo_pago', 'efectivo').strip()
        pagos = [
            {'metodo': metodo, 'monto': total_decimal}
        ]
        return metodo, pagos, None

@clientes_bp.route('/<int:id>/cobro_seleccionados', methods=['POST'])
@login_required
def cobro_seleccionados(id):
    cliente = Cliente.query.get_or_404(id)
    
    # Obtener IDs seleccionados desde formulario
    maneo_ids_raw = request.form.getlist('maneo_ids')
    if not maneo_ids_raw and request.form.get('maneo_ids_csv'):
        maneo_ids_raw = [x.strip() for x in request.form.get('maneo_ids_csv', '').split(',') if x.strip()]
        
    selected_ids = [int(x) for x in maneo_ids_raw if str(x).isdigit()]
    
    if not selected_ids:
        flash('Debes seleccionar al menos un producto para cobrar.', 'warning')
        return redirect(url_for('clientes_bp.estado_cuenta', id=cliente.id))
        
    maneos_a_cobrar = [m for m in cliente.maneos if m.estado == 'PENDIENTE' and m.id in selected_ids]
    
    if not maneos_a_cobrar:
        flash('No se encontraron maneos pendientes activos con los IDs seleccionados.', 'warning')
        return redirect(url_for('clientes_bp.estado_cuenta', id=cliente.id))

    total_a_cobrar = sum(m.subtotal_calculado for m in maneos_a_cobrar)
    total_unidades = sum(m.cantidad for m in maneos_a_cobrar)
    tipo_pago = request.form.get('tipo_pago', 'unico')
    imprimir_ticket = bool(request.form.get('imprimir_ticket'))
    hora_actual = obtener_hora_bogota()

    metodo_principal, pagos_list, error_msg = _procesar_pagos_venta(tipo_pago, total_a_cobrar, request.form)
    if error_msg:
        flash(error_msg, 'danger')
        return redirect(url_for('clientes_bp.estado_cuenta', id=cliente.id))

    try:
        nueva_venta = Sale(
            vendedor_id=current_user.id,
            monto_total=total_a_cobrar,
            metodo_pago=metodo_principal,
            fecha_venta=hora_actual
        )
        db.session.add(nueva_venta)
        db.session.flush()

        for m in maneos_a_cobrar:
            pu = float(m.valor_unitario_calculado)
            detalle = SaleDetail(
                sale_id=nueva_venta.id,
                product_id=m.product_id,
                variant_id=m.variant_id,
                cantidad_vendida=m.cantidad,
                precio_venta_final=pu
            )
            db.session.add(detalle)
            m.estado = 'FACTURADO'
            m.metodo_pago = metodo_principal
            m.fecha_resolucion = hora_actual

        for p in pagos_list:
            pago_obj = SalePayment(
                sale_id=nueva_venta.id,
                metodo_pago=p['metodo'],
                monto=p['monto']
            )
            db.session.add(pago_obj)

        db.session.commit()
        metodo_desc = "Pago Dividido (" + " + ".join([f"{p['metodo'].capitalize()} ${p['monto']:,.0f}" for p in pagos_list]) + ")" if len(pagos_list) > 1 else pagos_list[0]['metodo'].capitalize()
        flash(f'¡Cobro exitoso! Se liquidaron {len(maneos_a_cobrar)} productos ({total_unidades} uds) por un total de ${total_a_cobrar:,.0f} [{metodo_desc}].', 'success')

        if imprimir_ticket:
            return redirect(url_for('sales_bp.imprimir_ticket', sale_id=nueva_venta.id))

    except Exception as e:
        db.session.rollback()
        flash(f'Error al procesar el cobro de seleccionados: {str(e)}', 'danger')

    return redirect(url_for('clientes_bp.estado_cuenta', id=cliente.id))

@clientes_bp.route('/<int:id>/cobro_total', methods=['POST'])
@login_required
def cobro_total(id):
    cliente = Cliente.query.get_or_404(id)
    maneos_activos = [m for m in cliente.maneos if m.estado == 'PENDIENTE']

    if not maneos_activos:
        flash(f'"{cliente.nombre_o_razon_social}" no tiene maneos activos pendientes por cobrar.', 'info')
        return redirect(url_for('clientes_bp.estado_cuenta', id=cliente.id))

    total_a_cobrar = sum(m.subtotal_calculado for m in maneos_activos)
    total_unidades = sum(m.cantidad for m in maneos_activos)
    tipo_pago = request.form.get('tipo_pago', 'unico')
    imprimir_ticket = bool(request.form.get('imprimir_ticket'))
    hora_actual = obtener_hora_bogota()

    metodo_principal, pagos_list, error_msg = _procesar_pagos_venta(tipo_pago, total_a_cobrar, request.form)
    if error_msg:
        flash(error_msg, 'danger')
        return redirect(url_for('clientes_bp.estado_cuenta', id=cliente.id))

    try:
        # Crear una única venta consolidada para liquidar todos los maneos activos
        nueva_venta = Sale(
            vendedor_id=current_user.id,
            monto_total=total_a_cobrar,
            metodo_pago=metodo_principal,
            fecha_venta=hora_actual
        )
        db.session.add(nueva_venta)
        db.session.flush()

        for m in maneos_activos:
            pu = float(m.valor_unitario_calculado)
            detalle = SaleDetail(
                sale_id=nueva_venta.id,
                product_id=m.product_id,
                variant_id=m.variant_id,
                cantidad_vendida=m.cantidad,
                precio_venta_final=pu
            )
            db.session.add(detalle)

            m.estado = 'FACTURADO'
            m.metodo_pago = metodo_principal
            m.fecha_resolucion = hora_actual

        for p in pagos_list:
            pago_obj = SalePayment(
                sale_id=nueva_venta.id,
                metodo_pago=p['metodo'],
                monto=p['monto']
            )
            db.session.add(pago_obj)

        db.session.commit()
        metodo_desc = "Pago Dividido (" + " + ".join([f"{p['metodo'].capitalize()} ${p['monto']:,.0f}" for p in pagos_list]) + ")" if len(pagos_list) > 1 else pagos_list[0]['metodo'].capitalize()
        flash(f'¡Cobro total exitoso! Se liquidaron {len(maneos_activos)} productos ({total_unidades} uds) por un total de ${total_a_cobrar:,.0f} [{metodo_desc}].', 'success')

        if imprimir_ticket:
            return redirect(url_for('sales_bp.imprimir_ticket', sale_id=nueva_venta.id))

    except Exception as e:
        db.session.rollback()
        flash(f'Error al procesar el cobro total: {str(e)}', 'danger')

    return redirect(url_for('clientes_bp.estado_cuenta', id=cliente.id))
 
@clientes_bp.route('/<int:id>/abono', methods=['POST'])
@login_required
def registrar_abono(id):
    """
    Registra un abono de dinero a la cuenta del cliente/local.
    Aplica el abono bajo criterio FIFO (los maneos/artículos más antiguos se cubren primero).
    Permite abonos parciales sobre artículos individuales y registra la transacción en Caja.
    """
    cliente = Cliente.query.get_or_404(id)
    maneos_activos = Maneo.query.filter_by(cliente_id=cliente.id, estado='PENDIENTE').order_by(Maneo.fecha_prestamo.asc(), Maneo.id.asc()).all()

    if not maneos_activos:
        flash(f'"{cliente.nombre_o_razon_social}" no tiene deudas ni maneos pendientes.', 'info')
        if request.form.get('origen') == 'directorio':
            return redirect(url_for('clientes_bp.index'))
        return redirect(url_for('clientes_bp.estado_cuenta', id=cliente.id))

    saldo_total_pendiente = sum(Decimal(str(m.saldo_pendiente_calculado)) for m in maneos_activos)
    if saldo_total_pendiente <= Decimal('0'):
        flash(f'"{cliente.nombre_o_razon_social}" se encuentra al día.', 'info')
        if request.form.get('origen') == 'directorio':
            return redirect(url_for('clientes_bp.index'))
        return redirect(url_for('clientes_bp.estado_cuenta', id=cliente.id))

    # Parsear y validar monto del abono
    monto_raw = request.form.get('monto_abono', '0').replace('$', '').replace('.', '').replace(',', '').strip() or '0'
    try:
        monto_abono = Decimal(monto_raw)
    except Exception:
        flash('El monto de abono ingresado no es válido.', 'danger')
        if request.form.get('origen') == 'directorio':
            return redirect(url_for('clientes_bp.index'))
        return redirect(url_for('clientes_bp.estado_cuenta', id=cliente.id))

    if monto_abono <= Decimal('0'):
        flash('El monto de abono debe ser mayor a $0.', 'warning')
        if request.form.get('origen') == 'directorio':
            return redirect(url_for('clientes_bp.index'))
        return redirect(url_for('clientes_bp.estado_cuenta', id=cliente.id))

    if monto_abono > saldo_total_pendiente:
        flash(f'El monto del abono (${monto_abono:,.0f}) supera la deuda total pendiente (${saldo_total_pendiente:,.0f}).', 'warning')
        if request.form.get('origen') == 'directorio':
            return redirect(url_for('clientes_bp.index'))
        return redirect(url_for('clientes_bp.estado_cuenta', id=cliente.id))

    tipo_pago = request.form.get('tipo_pago', 'unico')
    notas = request.form.get('notas_abono', '').strip()
    imprimir_ticket = bool(request.form.get('imprimir_ticket'))
    hora_actual = obtener_hora_bogota()

    metodo_principal, pagos_list, error_msg = _procesar_pagos_venta(tipo_pago, monto_abono, request.form)
    if error_msg:
        flash(error_msg, 'danger')
        if request.form.get('origen') == 'directorio':
            return redirect(url_for('clientes_bp.index'))
        return redirect(url_for('clientes_bp.estado_cuenta', id=cliente.id))

    try:
        # 1. Registrar venta en Caja para registrar el ingreso real del dinero
        nueva_venta = Sale(
            vendedor_id=current_user.id,
            monto_total=monto_abono,
            metodo_pago=metodo_principal,
            fecha_venta=hora_actual
        )
        db.session.add(nueva_venta)
        db.session.flush()

        for p in pagos_list:
            pago_obj = SalePayment(
                sale_id=nueva_venta.id,
                metodo_pago=p['metodo'],
                monto=p['monto']
            )
            db.session.add(pago_obj)

        # 2. Registrar el Abono en la tabla histórica de abonos
        abono_record = AbonoCliente(
            cliente_id=cliente.id,
            usuario_id=current_user.id,
            sale_id=nueva_venta.id,
            monto=monto_abono,
            metodo_pago=metodo_principal,
            notas=notas if notas else None,
            fecha_abono=hora_actual
        )
        db.session.add(abono_record)

        # 3. Aplicar criterio FIFO a los maneos (desde el más antiguo)
        monto_restante = Decimal(str(monto_abono))
        articulos_liquidados = 0
        articulos_abonados_parcial = 0

        for m in maneos_activos:
            if monto_restante <= Decimal('0'):
                break

            saldo_m = Decimal(str(m.saldo_pendiente_calculado))
            if saldo_m <= Decimal('0'):
                continue

            if monto_restante >= saldo_m:
                # Se liquida este maneo completamente
                monto_aplicado = saldo_m
                m.abono_acumulado = Decimal(str(m.subtotal_calculado))
                m.estado = 'FACTURADO'
                m.metodo_pago = metodo_principal
                m.fecha_resolucion = hora_actual
                monto_restante -= monto_aplicado
                articulos_liquidados += 1

                detalle = SaleDetail(
                    sale_id=nueva_venta.id,
                    product_id=m.product_id,
                    variant_id=m.variant_id,
                    cantidad_vendida=max(1, m.cantidad),
                    precio_venta_final=float(monto_aplicado) / max(1, m.cantidad),
                    nombre_manual=f"{m.producto.nombre} (Liquidado con abono FIFO - {cliente.nombre_o_razon_social})"
                )
                db.session.add(detalle)
            else:
                # Abono parcial al maneo (permanece activo)
                monto_aplicado = monto_restante
                m.abono_acumulado = Decimal(str(m.abono_acumulado or 0)) + monto_aplicado
                monto_restante = Decimal('0')
                articulos_abonados_parcial += 1

                detalle = SaleDetail(
                    sale_id=nueva_venta.id,
                    product_id=m.product_id,
                    variant_id=m.variant_id,
                    cantidad_vendida=1,
                    precio_venta_final=float(monto_aplicado),
                    nombre_manual=f"Abono parcial a: {m.producto.nombre} ({cliente.nombre_o_razon_social})"
                )
                db.session.add(detalle)

        db.session.commit()

        resumen_txt = []
        if articulos_liquidados > 0:
            resumen_txt.append(f"{articulos_liquidados} producto(s) saldado(s) totalmente")
        if articulos_abonados_parcial > 0:
            resumen_txt.append(f"{articulos_abonados_parcial} con abono parcial activo")
        detalle_str = f" ({', '.join(resumen_txt)})" if resumen_txt else ""

        flash(f'¡Abono de ${monto_abono:,.0f} registrado exitosamente!{detalle_str}. Saldo pendiente restante: ${cliente.saldo_maneos_pendiente:,.0f}.', 'success')

        if imprimir_ticket:
            return redirect(url_for('sales_bp.imprimir_ticket', sale_id=nueva_venta.id))

    except Exception as e:
        db.session.rollback()
        flash(f'Error al registrar el abono: {str(e)}', 'danger')

    if request.form.get('origen') == 'directorio':
        return redirect(url_for('clientes_bp.index'))
    return redirect(url_for('clientes_bp.estado_cuenta', id=cliente.id))


