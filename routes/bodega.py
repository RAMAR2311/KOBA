from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app, jsonify
from flask_login import login_required, current_user
from decorators import any_bodega_required
from models import db, Cliente, FacturaBodega, AbonoBodega, Product, StockAdjustment, FacturaBodegaDetalle, obtener_hora_bogota
import os
from decimal import Decimal
from werkzeug.utils import secure_filename

bodega_bp = Blueprint('bodega_bp', __name__)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@bodega_bp.route('/dashboard')
@login_required
@any_bodega_required
def dashboard():
    if current_user.rol == 'vendedor_bodega':
        total_clientes = Cliente.query.filter_by(creado_por_id=current_user.id).count()
        facturas_recientes = FacturaBodega.query.filter_by(usuario_id=current_user.id).order_by(FacturaBodega.fecha_subida.desc()).limit(10).all()
        abonos_recientes = AbonoBodega.query.filter_by(usuario_id=current_user.id).order_by(AbonoBodega.fecha_abono.desc()).limit(10).all()
    else:
        # Rol 'bodega' o 'admin' ve todo
        total_clientes = Cliente.query.count()
        facturas_recientes = FacturaBodega.query.order_by(FacturaBodega.fecha_subida.desc()).limit(10).all()
        abonos_recientes = AbonoBodega.query.order_by(AbonoBodega.fecha_abono.desc()).limit(10).all()
    
    return render_template('bodega/dashboard.html', clientes_count=total_clientes, facturas=facturas_recientes, abonos=abonos_recientes)

@bodega_bp.route('/clientes/nuevo', methods=['GET', 'POST'])
@login_required
@any_bodega_required
def nuevo_cliente():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        documento = request.form.get('documento')
        telefono = request.form.get('telefono')
        email = request.form.get('email')
        direccion = request.form.get('direccion')

        if not nombre or not documento or not telefono:
            flash('Por favor completa los campos obligatorios: Nombre, Documento y Teléfono.', 'danger')
            return redirect(url_for('bodega_bp.nuevo_cliente'))

        if Cliente.query.filter_by(documento_o_nit=documento.strip()).first():
            flash('Ya existe un cliente registrado con ese Documento/NIT.', 'warning')
            return redirect(url_for('bodega_bp.nuevo_cliente'))

        nuevo = Cliente(
            nombre_o_razon_social=nombre.strip(),
            documento_o_nit=documento.strip(),
            telefono=telefono.strip(),
            email=email.strip() if email else None,
            direccion=direccion.strip() if direccion else None,
            creado_por_id=current_user.id
        )
        try:
            db.session.add(nuevo)
            db.session.commit()
            flash(f'Cliente {nombre} registrado exitosamente.', 'success')
            
            if request.args.get('from_factura'):
                return redirect(url_for('bodega_bp.nueva_factura', preselected_id=nuevo.id))
                
            return redirect(url_for('bodega_bp.dashboard'))
        except Exception as e:
            db.session.rollback()
            flash('Error al intentar registrar el cliente.', 'danger')

    return render_template('bodega/cliente_nuevo.html')

@bodega_bp.route('/clientes/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@any_bodega_required
def editar_cliente(id):
    cliente = Cliente.query.get_or_404(id)
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        documento = request.form.get('documento')
        telefono = request.form.get('telefono')
        email = request.form.get('email')
        direccion = request.form.get('direccion')

        if not nombre or not documento or not telefono:
            flash('Por favor completa los campos obligatorios: Nombre, Documento y Teléfono.', 'danger')
            return redirect(url_for('bodega_bp.editar_cliente', id=id))

        cliente_existente = Cliente.query.filter_by(documento_o_nit=documento.strip()).first()
        if cliente_existente and cliente_existente.id != id:
            flash('Ya existe otro cliente registrado con ese Documento/NIT.', 'warning')
            return redirect(url_for('bodega_bp.editar_cliente', id=id))

        cliente.nombre_o_razon_social = nombre.strip()
        cliente.documento_o_nit = documento.strip()
        cliente.telefono = telefono.strip()
        cliente.email = email.strip() if email else None
        cliente.direccion = direccion.strip() if direccion else None

        try:
            db.session.commit()
            flash(f'Cliente {nombre} actualizado exitosamente.', 'success')
            return redirect(url_for('bodega_bp.clientes'))
        except Exception as e:
            db.session.rollback()
            flash('Error al intentar actualizar el cliente.', 'danger')

    return render_template('bodega/cliente_editar.html', cliente=cliente)

@bodega_bp.route('/caja_rapida', methods=['GET', 'POST'])
@login_required
@any_bodega_required
def caja_rapida():
    if request.method == 'POST':
        import json
        from decimal import Decimal
        from datetime import datetime
        cliente_id = request.form.get('cliente_id')
        cart_data_str = request.form.get('cart_data', '{}')
        fecha_venta_str = request.form.get('fecha_venta')
        metodo_pago = request.form.get('metodo_pago', 'efectivo')
        monto_abono_str = request.form.get('monto_abono', '0').replace('.', '').replace(',', '')
        
        try:
            cart = json.loads(cart_data_str)
            if isinstance(cart, dict): # Fallback just in case
                cart = list(cart.values())
        except:
            cart = []
            
        if not cart:
            flash('El carrito está vacío.', 'danger')
            return redirect(url_for('bodega_bp.caja_rapida'))
            
        if not cliente_id:
            flash('Debes seleccionar un cliente.', 'danger')
            return redirect(url_for('bodega_bp.caja_rapida'))

        numero_factura_manual = request.form.get('numero_factura_manual')
        if numero_factura_manual and numero_factura_manual.strip():
            numero_factura = numero_factura_manual.strip().upper()
        else:
            ultima_factura = FacturaBodega.query.order_by(FacturaBodega.id.desc()).first()
            siguiente_num = ultima_factura.id + 1 if ultima_factura else 1
            numero_factura = f"POS-BOD-{siguiente_num:05d}"
            
        monto_total = Decimal(0)
        for item in cart:
            monto_total += Decimal(item.get('precio_final', 0)) * int(item.get('cantidad', 1))

        try:
            monto_abono = Decimal(monto_abono_str or 0)
        except:
            monto_abono = Decimal(0)

        modalidad = 'credito' if metodo_pago == 'credito' else 'contado'
        
        if modalidad == 'contado':
            monto_abono = monto_total
            estado_factura = 'Pagado'
        else:
            if monto_abono >= monto_total:
                estado_factura = 'Pagado'
            elif monto_abono > 0:
                estado_factura = 'Parcial'
            else:
                estado_factura = 'Pendiente'

        fecha_obj = None
        if fecha_venta_str:
            try:
                fecha_obj = datetime.strptime(fecha_venta_str, '%Y-%m-%d')
            except ValueError:
                pass

        try:
            nueva_fact = FacturaBodega(
                cliente_id=cliente_id,
                usuario_id=current_user.id,
                numero_factura=numero_factura,
                monto_total=monto_total,
                modalidad=modalidad,
                estado=estado_factura
            )
            
            if fecha_obj:
                ahora = obtener_hora_bogota()
                nueva_fact.fecha_subida = fecha_obj.replace(hour=ahora.hour, minute=ahora.minute, second=ahora.second)
                
            db.session.add(nueva_fact)
            db.session.flush()
            
            if monto_abono > 0:
                abono = AbonoBodega(
                    cliente_id=cliente_id,
                    factura_id=nueva_fact.id,
                    usuario_id=current_user.id,
                    monto=monto_abono,
                    metodo_pago=metodo_pago if metodo_pago != 'credito' else 'efectivo',
                    observacion=f"Abono Caja Rápida - Fac #{numero_factura}"
                )
                if fecha_obj:
                    abono.fecha_abono = nueva_fact.fecha_subida
                db.session.add(abono)
                
            for item in cart:
                producto = Product.query.get(item['product_id'])
                variant_id = item.get('variant_id')
                if producto:
                    cantidad = int(item['cantidad'])
                    precio_venta = Decimal(item['precio_final'])
                    es_obsequio = item.get('es_obsequio', False)
                    
                    if es_obsequio:
                        precio_venta = Decimal(0)
                        
                    variante = None
                    if variant_id:
                        from models import ProductVariant
                        variante = ProductVariant.query.get(variant_id)
                        
                    if variante:
                        if variante.cantidad_stock >= cantidad:
                            stock_ant = variante.cantidad_stock
                            variante.cantidad_stock -= cantidad
                            # Also reflect total stock change in product if needed
                            producto.cantidad_stock = sum([v.cantidad_stock for v in producto.variantes])
                            
                            ajuste = StockAdjustment(
                                product_id=producto.id,
                                admin_id=current_user.id,
                                tipo_movimiento=f"Venta Bodega Rápida #{numero_factura} (Subcat: {variante.nombre_variante})",
                                stock_anterior=stock_ant,
                                stock_nuevo=variante.cantidad_stock
                            )
                            db.session.add(ajuste)
                        else:
                            flash(f'Stock insuficiente para la variante {variante.nombre_variante}.', 'danger')
                            db.session.rollback()
                            return redirect(url_for('bodega_bp.caja_rapida'))
                    else:
                        if producto.cantidad_stock >= cantidad:
                            stock_ant = producto.cantidad_stock
                            producto.cantidad_stock -= cantidad
                            
                            ajuste = StockAdjustment(
                                product_id=producto.id,
                                admin_id=current_user.id,
                                tipo_movimiento=f"Venta Bodega Rápida #{numero_factura}",
                                stock_anterior=stock_ant,
                                stock_nuevo=producto.cantidad_stock
                            )
                            db.session.add(ajuste)
                        else:
                            flash(f'Stock insuficiente para el producto {producto.nombre}.', 'danger')
                            db.session.rollback()
                            return redirect(url_for('bodega_bp.caja_rapida'))
                        
                    detalle = FacturaBodegaDetalle(
                        factura_id=nueva_fact.id,
                        producto_id=producto.id,
                        variant_id=variant_id,
                        cantidad=cantidad,
                        precio_venta=precio_venta
                    )
                    db.session.add(detalle)
                    
            db.session.commit()
            flash(f'Venta rápida registrada con éxito. Factura: {numero_factura}', 'success')
            return redirect(url_for('bodega_bp.dashboard'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Ocurrió un error al registrar la venta.', 'danger')
            return redirect(url_for('bodega_bp.caja_rapida'))

    productos = Product.query.options(db.selectinload(Product.variantes)).filter_by(tipo_inventario='bodega').order_by(Product.nombre.asc()).all()
    clientes = Cliente.query.order_by(Cliente.nombre_o_razon_social.asc()).all()
    
    return render_template('bodega/caja_rapida.html', productos=productos, clientes=clientes)

@bodega_bp.route('/facturas/nueva', methods=['GET', 'POST'])
@login_required
@any_bodega_required
def nueva_factura():
    if request.method == 'POST':
        cliente_id = request.form.get('cliente_id')
        num_factura = request.form.get('numero_factura')
        monto_total_raw = request.form.get('monto_total', '0')
        monto_total = monto_total_raw.replace('.', '').replace(',', '') if isinstance(monto_total_raw, str) else monto_total_raw
        fecha_factura_str = request.form.get('fecha_factura')
        
        # Arrays of products and quantities
        productos_ids = request.form.getlist('producto_id[]')
        variantes_ids = request.form.getlist('variant_id[]')
        cantidades = request.form.getlist('cantidad[]')
        precios_unitarios_raw = request.form.getlist('precio_unitario[]')
        precios_unitarios = [p.replace('.', '').replace(',', '') for p in precios_unitarios_raw]
        
        if not productos_ids or not cantidades or not precios_unitarios:
            flash('Debes agregar al menos un producto a la factura.', 'danger')
            return redirect(url_for('bodega_bp.nueva_factura'))

        if len(productos_ids) != len(cantidades) or len(productos_ids) != len(precios_unitarios):
            flash('Error en los datos de los productos enviados.', 'danger')
            return redirect(url_for('bodega_bp.nueva_factura'))

        archivo_ruta_bd = None
        archivo = request.files.get('archivo_factura')
        if archivo and archivo.filename != '':
            if allowed_file(archivo.filename):
                filename = secure_filename(archivo.filename)
                unique_filename = f"fact_{cliente_id}_{num_factura}_{filename}"
                upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'facturas')
                
                os.makedirs(upload_path, exist_ok=True)
                file_path = os.path.join(upload_path, unique_filename)
                archivo.save(file_path)
                archivo_ruta_bd = f"uploads/facturas/{unique_filename}"
            else:
                flash('Tipo de archivo no permitido. Solo se permiten PDF e imágenes.', 'danger')
                return redirect(url_for('bodega_bp.nueva_factura'))

        from datetime import datetime
        fecha_obj = None
        if fecha_factura_str:
            try:
                # El formato de type="date" en HTML5 es YYYY-MM-DD
                fecha_obj = datetime.strptime(fecha_factura_str, '%Y-%m-%d')
            except ValueError:
                pass

        modalidad_pago = request.form.get('modalidad_pago', 'credito')
        monto_abono_inicial_str = request.form.get('monto_abono_inicial', '0').replace('.', '').replace(',', '')
        metodo_pago_abono = request.form.get('metodo_pago_abono', 'efectivo')
        
        try:
            monto_abono_inicial = Decimal(monto_abono_inicial_str or 0)
        except:
            monto_abono_inicial = Decimal(0)

        # Si es de contado y no pusieron abono manual, asumimos el total
        if modalidad_pago == 'contado' and monto_abono_inicial <= 0:
            monto_abono_inicial = Decimal(monto_total or 0)

        try:
            nueva_fact = FacturaBodega(
                cliente_id=cliente_id,
                usuario_id=current_user.id,
                numero_factura=num_factura,
                monto_total=Decimal(monto_total or 0),
                archivo_ruta=archivo_ruta_bd,
                modalidad=modalidad_pago
            )
            
            # Ajustar estado inicial según el abono
            if monto_abono_inicial >= Decimal(monto_total or 0):
                nueva_fact.estado = 'Pagado'
            elif monto_abono_inicial > 0:
                nueva_fact.estado = 'Parcial'
            else:
                nueva_fact.estado = 'Pendiente'

            if fecha_obj:
                ahora = obtener_hora_bogota()
                nueva_fact.fecha_subida = fecha_obj.replace(hour=ahora.hour, minute=ahora.minute, second=ahora.second)
                
            db.session.add(nueva_fact)
            db.session.flush() # Para obtener el ID de nueva_fact

            # Registrar el abono si existe
            if monto_abono_inicial > 0:
                abono = AbonoBodega(
                    cliente_id=cliente_id,
                    factura_id=nueva_fact.id,
                    usuario_id=current_user.id,
                    monto=monto_abono_inicial,
                    metodo_pago=metodo_pago_abono,
                    observacion=f'Abono inicial Factura #{num_factura}'
                )
                if fecha_obj:
                    abono.fecha_abono = nueva_fact.fecha_subida
                db.session.add(abono)
                
            # Procesar productos y descontar el stock
            for i in range(len(productos_ids)):
                p_id = productos_ids[i]
                cant = int(cantidades[i])
                precio_uni = Decimal(precios_unitarios[i] or 0)
                v_id_str = variantes_ids[i] if len(variantes_ids) > i else ""
                variant_id = int(v_id_str) if v_id_str.strip() else None

                producto = Product.query.get(p_id)
                variante = None
                
                if not producto:
                    db.session.rollback()
                    flash('Producto no encontrado.', 'danger')
                    return redirect(url_for('bodega_bp.nueva_factura'))

                if variant_id:
                    from models import ProductVariant
                    variante = ProductVariant.query.get(variant_id)
                    if not variante or variante.product_id != producto.id:
                        db.session.rollback()
                        flash(f'La subcategoría seleccionada no pertenece al producto {producto.nombre}.', 'danger')
                        return redirect(url_for('bodega_bp.nueva_factura'))
                    if variante.cantidad_stock < cant:
                        db.session.rollback()
                        flash(f'No hay stock suficiente para la subcategoría: {variante.nombre_variante}. Stock actual: {variante.cantidad_stock}', 'danger')
                        return redirect(url_for('bodega_bp.nueva_factura'))
                else:
                    if producto.cantidad_stock < cant:
                        db.session.rollback()
                        flash(f'No hay stock suficiente para el producto: {producto.nombre}. Stock actual: {producto.cantidad_stock}', 'danger')
                        return redirect(url_for('bodega_bp.nueva_factura'))
                
                # 1. Crear el Detalle
                detalle = FacturaBodegaDetalle(
                    factura_id=nueva_fact.id,
                    producto_id=producto.id,
                    variant_id=variant_id,
                    cantidad=cant,
                    precio_venta=precio_uni
                )
                db.session.add(detalle)
                
                # 2. Descontar Stock y Registrar Historial de Ajuste
                if variante:
                    stock_anterior = variante.cantidad_stock
                    variante.cantidad_stock -= cant
                    ajuste = StockAdjustment(
                        product_id=producto.id,
                        admin_id=current_user.id,
                        tipo_movimiento=f"Salida de subcategoría {variante.nombre_variante} por Factura Bodega #{num_factura}",
                        stock_anterior=stock_anterior,
                        stock_nuevo=variante.cantidad_stock
                    )
                else:
                    stock_anterior = producto.cantidad_stock
                    producto.cantidad_stock -= cant
                    ajuste = StockAdjustment(
                        product_id=producto.id,
                        admin_id=current_user.id,
                        tipo_movimiento=f"Salida por Factura Bodega #{num_factura}",
                        stock_anterior=stock_anterior,
                        stock_nuevo=producto.cantidad_stock
                    )
                db.session.add(ajuste)

            db.session.commit()
            flash('Factura guardada y stock de inventario descontado correctamente.', 'success')
            return redirect(url_for('bodega_bp.dashboard'))
            
        except Exception as e:
            db.session.rollback()
            flash('Ocurrió un error en la base de datos al guardar la factura o afectar el stock.', 'danger')

    clientes = Cliente.query.order_by(Cliente.nombre_o_razon_social).all()
    # Enviamos los productos disponibles a la vista, restringidos al inventario de bodega
    productos_disp = Product.query.filter_by(tipo_inventario='bodega').filter(Product.cantidad_stock > 0).order_by(Product.nombre).all()
    
    preselected_id = request.args.get('preselected_id', type=int)
    
    return render_template('bodega/factura_nueva.html', 
                           clientes=clientes, 
                           productos=productos_disp, 
                           preselected_id=preselected_id)

@bodega_bp.route('/api/producto/<path:sku>', methods=['GET'])
@login_required
@any_bodega_required
def api_buscar_producto_bodega(sku):
    producto = Product.query.filter_by(sku=sku, tipo_inventario='bodega').first()
    
    if not producto:
        return jsonify({'error': 'Código SKU no encontrado en bodega'}), 404
        
    return jsonify({
        'id': producto.id,
        'nombre': producto.nombre,
        'sku': producto.sku,
        'cantidad_stock': producto.total_stock,
        'precio_sugerido': float(producto.precio_sugerido),
        'precio_minimo': float(producto.precio_minimo),
        'precio_costo': float(producto.precio_costo),
        'variantes': [{"id": v.id, "nombre": v.nombre_variante, "stock": v.cantidad_stock, "precio_minimo": float(v.precio_minimo or producto.precio_minimo), "precio_limite": float(v.precio_costo or producto.precio_costo), "precio_sugerido": float(v.precio_sugerido or producto.precio_sugerido)} for v in producto.variantes]
    })

@bodega_bp.route('/clientes')
@login_required
@any_bodega_required
def clientes():
    if current_user.rol == 'vendedor_bodega':
        # Vendedor de bodega solo ve sus propios clientes en el listado de resumen
        lista_clientes = Cliente.query.filter_by(creado_por_id=current_user.id).order_by(Cliente.nombre_o_razon_social).all()
    else:
        # Bodega ve todo
        lista_clientes = Cliente.query.order_by(Cliente.nombre_o_razon_social).all()
    return render_template('bodega/clientes.html', clientes=lista_clientes)

@bodega_bp.route('/clientes/<int:id>')
@login_required
@any_bodega_required
def cliente_detalle(id):
    cliente = Cliente.query.get_or_404(id)
    return render_template('bodega/cliente_detalle.html', cliente=cliente)

@bodega_bp.route('/facturas/<int:factura_id>/abono', methods=['POST'])
@login_required
@any_bodega_required
def nuevo_abono(factura_id):
    factura = FacturaBodega.query.get_or_404(factura_id)
    monto_abono_str = request.form.get('monto_abono', '0').replace('.', '').replace(',', '')
    try:
        monto_abono = Decimal(monto_abono_str)
    except:
        monto_abono = Decimal(0)
    metodo_pago = request.form.get('metodo_pago', 'efectivo')
    observacion = request.form.get('observacion', '')

    if monto_abono <= 0:
        flash('El monto del abono debe ser mayor a cero.', 'danger')
        return redirect(url_for('bodega_bp.cliente_detalle', id=factura.cliente_id))

    if monto_abono > factura.saldo_pendiente:
        flash(f'El monto supera el saldo pendiente (${factura.saldo_pendiente}).', 'danger')
        return redirect(url_for('bodega_bp.cliente_detalle', id=factura.cliente_id))

    abono = AbonoBodega(
        cliente_id=factura.cliente_id,
        factura_id=factura.id,
        usuario_id=current_user.id,
        monto=monto_abono,
        metodo_pago=metodo_pago,
        observacion=observacion
    )
    
    try:
        db.session.add(abono)
        db.session.commit()
        
        # Validar si el saldo quedó en cero
        if factura.saldo_pendiente <= 0:
            factura.estado = 'Pagado'
        else:
            factura.estado = 'Parcial'
        db.session.commit()

        flash(f'Abono de ${monto_abono} registrado correctamente a la factura #{factura.numero_factura}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Hubo un error al registrar el abono.', 'danger')

    return redirect(url_for('bodega_bp.cliente_detalle', id=factura.cliente_id))

@bodega_bp.route('/clientes/<int:cliente_id>/abono_global', methods=['POST'])
@login_required
@any_bodega_required
def abono_global(cliente_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    monto_abono_str = request.form.get('monto_abono', '0').replace('.', '').replace(',', '')
    try:
        monto_abono = Decimal(monto_abono_str)
    except:
        monto_abono = Decimal(0)
    metodo_pago = request.form.get('metodo_pago', 'efectivo')
    observacion_base = request.form.get('observacion', '')
    
    fecha_str = request.form.get('fecha') or request.form.get('fecha_abono')
    fecha_dt = None
    if fecha_str:
        from datetime import datetime
        try:
            fecha_temp = datetime.strptime(fecha_str, '%Y-%m-%d')
            ahora = obtener_hora_bogota()
            fecha_dt = fecha_temp.replace(hour=ahora.hour, minute=ahora.minute, second=ahora.second)
        except ValueError:
            pass

    if monto_abono <= 0:
        flash('El monto del abono debe ser mayor a cero.', 'danger')
        return redirect(url_for('bodega_bp.cliente_detalle', id=cliente_id))

    monto_restante = monto_abono
    
    # 1. Buscar todas las facturas a crédito no pagadas, de la más vieja a la más nueva
    facturas_pendientes = FacturaBodega.query.filter_by(cliente_id=cliente.id, modalidad='credito')\
        .filter(FacturaBodega.estado != 'Pagado')\
        .order_by(FacturaBodega.fecha_subida.asc()).all()

    try:
        if not facturas_pendientes:
            # Si no hay facturas pendientes, se registra como un abono a cuenta (saldo a favor)
            abono = AbonoBodega(
                cliente_id=cliente.id,
                usuario_id=current_user.id,
                monto=monto_abono,
                metodo_pago=metodo_pago,
                observacion=observacion_base or "Abono global a cuenta (Sin facturas pendientes)"
            )
            if fecha_dt:
                abono.fecha_abono = fecha_dt
            db.session.add(abono)
        else:
            # 2. Aplicar el pago en cascada
            for f in facturas_pendientes:
                if monto_restante <= 0:
                    break
                
                deuda = f.saldo_pendiente
                if deuda <= 0:
                    continue
                
                pago_aplicado = min(monto_restante, deuda)
                
                obs_cascada = f"Pago cascada Fac. #{f.numero_factura}"
                if observacion_base:
                    obs_cascada = f"{observacion_base} | {obs_cascada}"

                nuevo_abono_item = AbonoBodega(
                    cliente_id=cliente.id,
                    factura_id=f.id,
                    usuario_id=current_user.id,
                    monto=pago_aplicado,
                    metodo_pago=metodo_pago,
                    observacion=obs_cascada
                )
                if fecha_dt:
                    nuevo_abono_item.fecha_abono = fecha_dt
                db.session.add(nuevo_abono_item)
                
                monto_restante -= pago_aplicado
                
                # Actualizar estado de la factura basándonos en si la deuda se cubrió
                if pago_aplicado >= deuda:
                    f.estado = 'Pagado'
                else:
                    f.estado = 'Parcial'

            # 3. Si después de recorrer todas las facturas aún queda dinero, se registra el excedente como abono a cuenta
            if monto_restante > 0:
                abono_excedente = AbonoBodega(
                    cliente_id=cliente.id,
                    usuario_id=current_user.id,
                    monto=monto_restante,
                    metodo_pago=metodo_pago,
                    observacion=f"{observacion_base} (Excedente después de pagar facturas)" if observacion_base else "Excedente de pago global"
                )
                if fecha_dt:
                    abono_excedente.fecha_abono = fecha_dt
                db.session.add(abono_excedente)

        db.session.commit()
        flash(f'Se procesó el pago global de ${monto_abono}. Las deudas se saldaron de la más antigua a la más reciente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Hubo un error al procesar el abono en cascada.', 'danger')

    return redirect(url_for('bodega_bp.cliente_detalle', id=cliente_id))

@bodega_bp.route('/clientes/<int:cliente_id>/saldo_anterior', methods=['POST'])
@login_required
@any_bodega_required
def registrar_saldo_anterior(cliente_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    monto_str = request.form.get('monto', '0').replace('.', '').replace(',', '')
    try:
        monto = Decimal(monto_str)
    except:
        monto = Decimal(0)
    
    nota = request.form.get('nota', 'Saldo Anterior / Facturas Viejas')
    fecha_str = request.form.get('fecha')

    if monto <= 0:
        flash('El monto debe ser mayor a cero.', 'danger')
        return redirect(url_for('bodega_bp.cliente_detalle', id=cliente_id))

    nueva_fact = FacturaBodega(
        cliente_id=cliente.id,
        usuario_id=current_user.id,
        numero_factura="SALDO-ANTERIOR",
        monto_total=monto,
        modalidad='credito',
        estado='Pendiente'
    )
    
    if fecha_str:
        from datetime import datetime
        try:
            fecha_dt = datetime.strptime(fecha_str, '%Y-%m-%d')
            ahora = obtener_hora_bogota()
            nueva_fact.fecha_subida = fecha_dt.replace(hour=ahora.hour, minute=ahora.minute, second=ahora.second)
        except ValueError:
            pass

    try:
        db.session.add(nueva_fact)
        db.session.commit()
        flash(f'Saldo anterior de ${monto} registrado correctamente para {cliente.nombre_o_razon_social}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error al registrar el saldo anterior.', 'danger')

    return redirect(url_for('bodega_bp.cliente_detalle', id=cliente_id))

@bodega_bp.route('/abonos/<int:abono_id>/editar', methods=['POST'])
@login_required
@any_bodega_required
def editar_abono(abono_id):
    abono = AbonoBodega.query.get_or_404(abono_id)
    cliente_id = abono.cliente_id
    
    monto_abono_str = request.form.get('monto_abono', '0').replace('.', '').replace(',', '')
    try:
        monto_nuevo = Decimal(monto_abono_str)
    except:
        monto_nuevo = abono.monto

    metodo_pago = request.form.get('metodo_pago', abono.metodo_pago)
    observacion = request.form.get('observacion', abono.observacion)
    fecha_str = request.form.get('fecha_abono') or request.form.get('fecha')

    if monto_nuevo <= 0:
        flash('El monto del abono debe ser mayor a cero.', 'danger')
        return redirect(url_for('bodega_bp.cliente_detalle', id=cliente_id))

    # Si el abono está asociado a una factura, validar que el nuevo monto no exceda el límite
    if abono.factura:
        # El saldo pendiente real es: monto_total - (suma_otros_abonos)
        otros_abonos = sum(a.monto for a in abono.factura.abonos if a.id != abono.id)
        max_permitido = abono.factura.monto_total - otros_abonos
        if monto_nuevo > max_permitido:
            flash(f'El monto supera el saldo pendiente de la factura (${max_permitido}).', 'danger')
            return redirect(url_for('bodega_bp.cliente_detalle', id=cliente_id))

    try:
        abono.monto = monto_nuevo
        abono.metodo_pago = metodo_pago
        abono.observacion = observacion
        
        if fecha_str:
            from datetime import datetime
            try:
                nueva_fecha = datetime.strptime(fecha_str, '%Y-%m-%d')
                # Mantener la hora si es posible
                if abono.fecha_abono:
                    abono.fecha_abono = abono.fecha_abono.replace(year=nueva_fecha.year, month=nueva_fecha.month, day=nueva_fecha.day)
                else:
                    abono.fecha_abono = nueva_fecha
            except ValueError:
                pass

        db.session.commit()

        # Re-evaluar estado de la factura si existe
        if abono.factura:
            if abono.factura.saldo_pendiente <= 0:
                abono.factura.estado = 'Pagado'
            elif abono.factura.saldo_pendiente < abono.factura.monto_total:
                abono.factura.estado = 'Parcial'
            else:
                abono.factura.estado = 'Pendiente'
            db.session.commit()

        flash('Abono actualizado correctamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error al actualizar el abono.', 'danger')

    return redirect(url_for('bodega_bp.cliente_detalle', id=cliente_id))

@bodega_bp.route('/abonos/<int:abono_id>/eliminar', methods=['POST'])
@login_required
@any_bodega_required
def eliminar_abono(abono_id):
    abono = AbonoBodega.query.get_or_404(abono_id)
    cliente_id = abono.cliente_id
    factura = abono.factura
    
    try:
        db.session.delete(abono)
        db.session.commit()
        
        # Re-evaluar estado de la factura si existe
        if factura:
            if factura.saldo_pendiente <= 0:
                factura.estado = 'Pagado'
            elif factura.saldo_pendiente < factura.monto_total:
                factura.estado = 'Parcial'
            else:
                factura.estado = 'Pendiente'
            db.session.commit()
            
        flash('Abono eliminado correctamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error al eliminar el abono.', 'danger')

    return redirect(url_for('bodega_bp.cliente_detalle', id=cliente_id))

@bodega_bp.route('/facturas/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@any_bodega_required
def editar_factura(id):
    factura = FacturaBodega.query.get_or_404(id)
    if request.method == 'POST':
        factura.numero_factura = request.form.get('numero_factura')
        
        monto_total_str = request.form.get('monto_total', '0').replace('.', '').replace(',', '')
        try:
            factura.monto_total = Decimal(monto_total_str)
        except:
            pass

        fecha_factura_str = request.form.get('fecha_factura')
        if fecha_factura_str:
            from datetime import datetime
            try:
                # Conservar la hora original
                nueva_fecha = datetime.strptime(fecha_factura_str, '%Y-%m-%d')
                factura.fecha_subida = factura.fecha_subida.replace(year=nueva_fecha.year, month=nueva_fecha.month, day=nueva_fecha.day)
            except ValueError:
                pass

        factura.modalidad = request.form.get('modalidad_pago', factura.modalidad)
        
        archivo = request.files.get('archivo_factura')
        if archivo and archivo.filename != '':
            if allowed_file(archivo.filename):
                filename = secure_filename(archivo.filename)
                unique_filename = f"fact_{factura.cliente_id}_{factura.numero_factura}_{filename}"
                upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'facturas')
                os.makedirs(upload_path, exist_ok=True)
                file_path = os.path.join(upload_path, unique_filename)
                archivo.save(file_path)
                factura.archivo_ruta = f"uploads/facturas/{unique_filename}"
            else:
                flash('Tipo de archivo no permitido.', 'danger')

        try:
            # Reevaluar estado
            if factura.saldo_pendiente <= 0:
                factura.estado = 'Pagado'
            elif factura.saldo_pendiente < factura.monto_total:
                factura.estado = 'Parcial'
            else:
                factura.estado = 'Pendiente'
            db.session.commit()
            flash('Factura actualizada correctamente.', 'success')
            return redirect(url_for('bodega_bp.cliente_detalle', id=factura.cliente_id))
        except Exception as e:
            db.session.rollback()
            flash('Error al actualizar la factura.', 'danger')

    return render_template('bodega/factura_editar.html', factura=factura)

@bodega_bp.route('/facturas/<int:id>/eliminar', methods=['POST'])
@login_required
@any_bodega_required
def eliminar_factura(id):
    factura = FacturaBodega.query.get_or_404(id)
    cliente_id = factura.cliente_id
    num_factura = factura.numero_factura
    
    try:
        # Devolver stock
        for det in factura.detalles:
            if det.variante:
                stock_anterior = det.variante.cantidad_stock
                det.variante.cantidad_stock += det.cantidad
                ajuste = StockAdjustment(
                    product_id=det.producto_id,
                    admin_id=current_user.id,
                    tipo_movimiento=f"Reintegro por eliminación de Factura Bodega #{num_factura}",
                    stock_anterior=stock_anterior,
                    stock_nuevo=det.variante.cantidad_stock
                )
                db.session.add(ajuste)
            else:
                stock_anterior = det.producto.cantidad_stock
                det.producto.cantidad_stock += det.cantidad
                ajuste = StockAdjustment(
                    product_id=det.producto_id,
                    admin_id=current_user.id,
                    tipo_movimiento=f"Reintegro por eliminación de Factura Bodega #{num_factura}",
                    stock_anterior=stock_anterior,
                    stock_nuevo=det.producto.cantidad_stock
                )
                db.session.add(ajuste)
        
        db.session.delete(factura)
        db.session.commit()
        flash(f'Factura #{num_factura} eliminada y stock reintegrado correctamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error al intentar eliminar la factura.', 'danger')

    return redirect(url_for('bodega_bp.cliente_detalle', id=cliente_id))

@bodega_bp.route('/clientes/<int:id>/eliminar', methods=['POST'])
@login_required
@any_bodega_required
def eliminar_cliente(id):
    cliente = Cliente.query.get_or_404(id)

    # Seguridad: No permitir eliminar clientes que tengan deuda pendiente
    if cliente.deuda_total > 0:
        flash(f'No se puede eliminar a "{cliente.nombre_o_razon_social}" porque tiene una deuda pendiente de ${cliente.deuda_total}.', 'danger')
        return redirect(url_for('bodega_bp.clientes'))

    nombre = cliente.nombre_o_razon_social
    try:
        # Eliminar facturas asociadas (y sus abonos/detalles por cascade)
        for factura in cliente.facturas:
            db.session.delete(factura)
        db.session.delete(cliente)
        db.session.commit()
        flash(f'Cliente "{nombre}" eliminado exitosamente del directorio.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error al intentar eliminar el cliente.', 'danger')

    return redirect(url_for('bodega_bp.clientes'))

@bodega_bp.route('/clientes/api/search')
@login_required
@any_bodega_required
def api_search_clientes():
    query = request.args.get('q', '').strip()
    
    if len(query) < 2:
        return jsonify([])
    
    from sqlalchemy import or_
    # Permitir que todos los perfiles busquen a todos los clientes para facilitar abonos e informes
    query_base = Cliente.query

    clientes_match = query_base.filter(
        or_(
            Cliente.nombre_o_razon_social.ilike(f'%{query}%'),
            Cliente.documento_o_nit.ilike(f'%{query}%')
        )
    ).limit(10).all()
    
    results = []
    for c in clientes_match:
        results.append({
            'id': c.id,
            'nombre': c.nombre_o_razon_social,
            'documento': c.documento_o_nit,
            'deuda': float(c.deuda_total),
            'url': url_for('bodega_bp.cliente_detalle', id=c.id)
        })
    
    return jsonify(results)

@bodega_bp.route('/facturas/api/search')
@login_required
@any_bodega_required
def api_search_facturas():
    query = request.args.get('q', '').strip()
    
    if len(query) < 2:
        return jsonify([])
    
    from sqlalchemy import or_
    # Filtrar según el rol (vendedor solo sus facturas, bodega/admin todas)
    if current_user.rol == 'vendedor_bodega':
        query_base = FacturaBodega.query.filter_by(usuario_id=current_user.id)
    else:
        query_base = FacturaBodega.query

    facturas_match = query_base.filter(
        FacturaBodega.numero_factura.ilike(f'%{query}%')
    ).limit(10).all()
    
    results = []
    for f in facturas_match:
        results.append({
            'id': f.id,
            'numero': f.numero_factura,
            'cliente': f.cliente.nombre_o_razon_social,
            'estado': f.estado,
            'fecha': f.fecha_subida.strftime('%d/%m/%Y'),
            'url': url_for('bodega_bp.cliente_detalle', id=f.cliente.id)
        })
    
    return jsonify(results)

@bodega_bp.route('/abonos/modulo', methods=['GET'])
@login_required
@any_bodega_required
def modulo_abonos():
    return render_template('bodega/modulo_abonos.html')
