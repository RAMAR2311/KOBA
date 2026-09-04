import os
from werkzeug.utils import secure_filename
from flask import current_app, Blueprint, render_template, request, redirect, url_for, flash, abort, send_file, jsonify
from flask_login import login_required, current_user
from models import db, Product, StockAdjustment, ProductVariant
from decorators import admin_required, admin_or_bodega_required
import pandas as pd
from io import BytesIO

inventory_bp = Blueprint('inventory_bp', __name__)

@inventory_bp.route('/', methods=['GET'])
@login_required
@admin_or_bodega_required
def index():
    tipo = 'bodega' if current_user.rol == 'bodega' else 'tienda'
    page = request.args.get('page', 1, type=int)
    per_page = 20

    # Paginación del listado principal
    paginacion = Product.query.filter_by(tipo_inventario=tipo).order_by(Product.nombre).paginate(
        page=page, per_page=per_page, error_out=False
    )
    productos = paginacion.items

    # --- KPIs de Inventario ---
    # Se calcula sobre TODO el inventario (no solo la página actual)
    todos = Product.query.filter_by(tipo_inventario=tipo).all()
    total_productos = len(todos)

    valor_costo = 0.0
    valor_sugerido = 0.0
    total_unidades_fisicas = 0
    for p in todos:
        if p.variantes:
            for v in p.variantes:
                costo = float(v.precio_costo or p.precio_costo or 0)
                sugerido = float(v.precio_sugerido or p.precio_sugerido or 0)
                stock = v.cantidad_stock or 0
                valor_costo += costo * stock
                valor_sugerido += sugerido * stock
                total_unidades_fisicas += stock
        else:
            costo = float(p.precio_costo or 0)
            sugerido = float(p.precio_sugerido or 0)
            stock = p.cantidad_stock or 0
            valor_costo += costo * stock
            valor_sugerido += sugerido * stock
            total_unidades_fisicas += stock

    return render_template(
        'inventory/index.html',
        productos=productos,
        paginacion=paginacion,
        total_productos=total_productos,
        valor_costo=valor_costo,
        valor_sugerido=valor_sugerido,
        total_unidades_fisicas=total_unidades_fisicas
    )

@inventory_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@admin_or_bodega_required
def nuevo():
    if request.method == 'POST':
        # --- Manejo de Imagen ---
        imagen_filename = None
        if 'imagen' in request.files:
            file = request.files['imagen']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                try:
                    file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                    imagen_filename = filename
                except OSError as e:
                    flash(f'Error al guardar la imagen (Revisa permisos o la ruta en el VPS): {str(e)}', 'danger')
                    return redirect(url_for('inventory_bp.nuevo'))

        # La instanciación agrupa todos los parámetros del nuevo producto
        tipo = 'bodega' if current_user.rol == 'bodega' else 'tienda'
        
        # Recibir variantes
        v_nombres = request.form.getlist('v_nombre[]')
        v_stocks = request.form.getlist('v_stock[]')
        v_costos = request.form.getlist('v_costo[]')
        v_mins = request.form.getlist('v_min[]')
        v_sugs = request.form.getlist('v_sug[]')

        # Si hay variantes, el stock base del producto maestro se ignora o se pone en 0
        stock_base = 0 if v_nombres else int(request.form.get('cantidad_stock') or 0)

        # Validar unicidad del SKU antes de guardar
        sku_candidato = request.form.get('sku', '').strip()
        if not sku_candidato:
            flash('Debes ingresar un código SKU para el producto.', 'warning')
            return render_template('inventory/form.html')

        prod_existente = Product.query.filter_by(sku=sku_candidato).first()
        if prod_existente:
            flash(f'El código SKU "{sku_candidato}" ya existe en el inventario (pertenece a "{prod_existente.nombre}"). Cada producto debe tener un SKU único.', 'warning')
            return render_template('inventory/form.html')

        nuevo_prod = Product(
            sku=sku_candidato,
            nombre=request.form.get('nombre').strip(),
            tipo_inventario=tipo,
            cantidad_stock=stock_base,
            precio_costo=float(request.form.get('precio_costo') or 0.0),
            precio_minimo=float(request.form.get('precio_minimo') or 0.0),
            precio_sugerido=float(request.form.get('precio_sugerido') or 0.0),
            imagen=imagen_filename,
            observacion=request.form.get('observacion')
        )
        
        try:
            db.session.add(nuevo_prod)
            db.session.flush() # Para obtener el ID del producto
            
            # Crear variantes si existen
            for i in range(len(v_nombres)):
                if not v_nombres[i]: continue
                nueva_v = ProductVariant(
                    product_id=nuevo_prod.id,
                    nombre_variante=v_nombres[i],
                    cantidad_stock=int(v_stocks[i] or 0),
                    precio_costo=float(v_costos[i]) if v_costos[i] else nuevo_prod.precio_costo,
                    precio_minimo=float(v_mins[i]) if v_mins[i] else nuevo_prod.precio_minimo,
                    precio_sugerido=float(v_sugs[i]) if v_sugs[i] else nuevo_prod.precio_sugerido
                )
                db.session.add(nueva_v)

            db.session.commit()
            
            # Crear ajuste inicial automáticamente en el Kardex
            ajuste_inicial = StockAdjustment(
                product_id=nuevo_prod.id,
                admin_id=current_user.id,
                tipo_movimiento='Creación Inicial' + (' (con Variantes)' if v_nombres else ''),
                stock_anterior=0,
                stock_nuevo=nuevo_prod.total_stock
            )
            db.session.add(ajuste_inicial)
            db.session.commit()

            flash('Producto Maestro y sus subcategorías creados exitosamente.', 'success')
            return redirect(url_for('inventory_bp.index'))
        except Exception as e:
            db.session.rollback()
            if 'unique' in str(e).lower() and 'sku' in str(e).lower():
                flash(f'El SKU "{sku_candidato}" ya se encuentra registrado. Utiliza un código diferente.', 'danger')
            else:
                flash(f'Error al intentar guardar el producto: {str(e)}', 'danger')
            
    return render_template('inventory/form.html')

@inventory_bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_or_bodega_required
def editar_producto(id):
    # get_or_404 protege la ruta en caso de que se envíe un ID inexistente en la URL
    producto = Product.query.get_or_404(id)
    tipo = 'bodega' if current_user.rol in ['bodega', 'vendedor_bodega'] else 'tienda'
    if current_user.rol != 'admin' and producto.tipo_inventario != tipo:
        abort(403)
    
    if request.method == 'POST':
        stock_total_anterior = producto.total_stock
        
        # Actualizar Imagen si se sube una nueva
        print("DEBUG: Processing POST request for /editar/" + str(id))
        if 'imagen' in request.files:
            file = request.files['imagen']
            print("DEBUG: 'imagen' found in request.files. filename:", file.filename)
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                print("DEBUG: secure_filename is:", filename)
                try:
                    file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                    print("DEBUG: file saved successfully to:", os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                    producto.imagen = filename
                except OSError as e:
                    print("DEBUG: OSError saving file:", e)
                    flash(f'Error al guardar la imagen (Revisa permisos o la ruta en el VPS): {str(e)}', 'danger')
                    return redirect(url_for('inventory_bp.editar_producto', id=id))
        else:
            print("DEBUG: 'imagen' NOT found in request.files")
                
                
        # Datos básicos con validación de SKU único
        nuevo_sku = request.form.get('sku', '').strip()
        if not nuevo_sku:
            flash('El código SKU no puede estar vacío.', 'warning')
            return redirect(url_for('inventory_bp.editar_producto', id=id))

        otro_con_sku = Product.query.filter(Product.sku == nuevo_sku, Product.id != id).first()
        if otro_con_sku:
            flash(f'El SKU "{nuevo_sku}" ya pertenece a otro producto ("{otro_con_sku.nombre}"). Los códigos SKU deben ser únicos.', 'warning')
            return redirect(url_for('inventory_bp.editar_producto', id=id))

        producto.sku = nuevo_sku
        producto.nombre = request.form.get('nombre').strip()
        producto.precio_costo = float(request.form.get('precio_costo') or 0.0)
        producto.precio_minimo = float(request.form.get('precio_minimo') or 0.0)
        producto.precio_sugerido = float(request.form.get('precio_sugerido') or 0.0)
        producto.observacion = request.form.get('observacion')
        
        # Sincronización de Variantes
        v_ids = request.form.getlist('variant_id[]')
        v_nombres = request.form.getlist('v_nombre[]')
        v_stocks = request.form.getlist('v_stock[]')
        v_costos = request.form.getlist('v_costo[]')
        v_mins = request.form.getlist('v_min[]')
        v_sugs = request.form.getlist('v_sug[]')

        ids_en_formulario = [int(vid) for vid in v_ids if vid]
        
        # 1. Eliminar las que ya no están en el formulario
        for v_existente in producto.variantes[:]:
            if v_existente.id not in ids_en_formulario:
                db.session.delete(v_existente)
        
        # 2. Actualizar o crear
        if not v_nombres:
            # Si no hay variantes, el stock es el base
            producto.cantidad_stock = int(request.form.get('cantidad_stock') or 0)
        else:
            # Si hay variantes, el stock base es 0
            producto.cantidad_stock = 0
            for i in range(len(v_nombres)):
                nombre_v = v_nombres[i]
                if not nombre_v: continue
                
                vid = v_ids[i] if i < len(v_ids) else None
                stock_v = int(v_stocks[i] or 0)
                costo_v = float(v_costos[i]) if v_costos[i] else producto.precio_costo
                min_v = float(v_mins[i]) if v_mins[i] else producto.precio_minimo
                sug_v = float(v_sugs[i]) if v_sugs[i] else producto.precio_sugerido

                if vid:
                    # Actualizar existente
                    v_obj = ProductVariant.query.get(int(vid))
                    if v_obj:
                        v_obj.nombre_variante = nombre_v
                        v_obj.cantidad_stock = stock_v
                        v_obj.precio_costo = costo_v
                        v_obj.precio_minimo = min_v
                        v_obj.precio_sugerido = sug_v
                else:
                    # Crear nueva
                    nueva_v = ProductVariant(
                        product_id=producto.id,
                        nombre_variante=nombre_v,
                        cantidad_stock=stock_v,
                        precio_costo=costo_v,
                        precio_minimo=min_v,
                        precio_sugerido=sug_v
                    )
                    db.session.add(nueva_v)

        try:
            print("DEBUG: Attempting db.session.commit()")
            db.session.commit()
            print("DEBUG: db.session.commit() successful")
            
            # Registrar ajuste de stock si el TOTAL cambió
            stock_total_nuevo = producto.total_stock
            if stock_total_anterior != stock_total_nuevo:
                ajuste = StockAdjustment(
                    product_id=producto.id,
                    admin_id=current_user.id,
                    tipo_movimiento='Ajuste en Edición Maestro',
                    stock_anterior=stock_total_anterior,
                    stock_nuevo=stock_total_nuevo
                )
                db.session.add(ajuste)
                db.session.commit()
                
            flash('Producto Maestro actualizado correctamente.', 'success')
            return redirect(url_for('inventory_bp.index'))
        except Exception as e:
            print("DEBUG: Exception during commit! Exception:", str(e))
            db.session.rollback()
            if 'unique' in str(e).lower() and 'sku' in str(e).lower():
                flash('No se pudo actualizar: El código SKU ya pertenece a otro producto. Cada producto debe tener un SKU único.', 'danger')
            else:
                flash(f'Error en la base de datos: {str(e)}', 'danger')

    # El objeto producto se pasa a Jinja para auto-poblar (pre-llenar) el formulario en modo edición
    return render_template('inventory/form.html', producto=producto)

@inventory_bp.route('/historial-ajustes')
@login_required
@admin_or_bodega_required
def historial_ajustes():
    tipo = 'bodega' if current_user.rol == 'bodega' else 'tienda'
    ajustes = StockAdjustment.query.join(Product).filter(Product.tipo_inventario == tipo).order_by(StockAdjustment.fecha_ajuste.desc()).all()
    return render_template('inventory/historial_ajustes.html', ajustes=ajustes)

@inventory_bp.route('/ver/<int:id>', methods=['GET'])
@login_required
@admin_or_bodega_required
def ver_producto(id):
    producto = Product.query.get_or_404(id)
    tipo = 'bodega' if current_user.rol == 'bodega' else 'tienda'
    if producto.tipo_inventario != tipo:
        abort(403)
    ajustes = StockAdjustment.query.filter_by(product_id=id).order_by(StockAdjustment.fecha_ajuste.desc()).all()
    return render_template('inventory/ver.html', producto=producto, ajustes=ajustes)

@inventory_bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_or_bodega_required
def eliminar_producto(id):
    producto = Product.query.get_or_404(id)
    tipo = 'bodega' if current_user.rol == 'bodega' else 'tienda'
    
    if producto.tipo_inventario != tipo:
        abort(403)
        
    from models import SaleDetail, Maneo, FacturaBodegaDetalle
    
    # 1. Validación de seguridad en cascada (No eliminar lo que tiene historia financiera/logística)
    if SaleDetail.query.filter_by(product_id=producto.id).first():
        flash('Acción denegada: El producto ya está vinculado a Historial de Ventas. Sugerencia: Ajustar stock a 0.', 'warning')
        return redirect(url_for('inventory_bp.index'))
        
    if Maneo.query.filter_by(product_id=producto.id).first():
        flash('Acción denegada: El producto tiene registros históticos en Maneos (Préstamos).', 'warning')
        return redirect(url_for('inventory_bp.index'))
        
    if FacturaBodegaDetalle.query.filter_by(producto_id=producto.id).first():
        flash('Acción denegada: El producto forma parte del detalle de una Factura Asignada.', 'warning')
        return redirect(url_for('inventory_bp.index'))
        
    try:
        # 2. Purgar dependencias suaves (Ajustes de Kardex)
        for ajuste in producto.ajustes_stock:
            db.session.delete(ajuste)
            
        # 3. Eliminar el producto madre (las Variantes se van automáticamente por regla delete-orphan de SQLAlchemy)
        nombre = producto.nombre
        db.session.delete(producto)
        db.session.commit()
        flash(f'Producto "{nombre}" fue borrado permanentemente del inventario.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Ocurrió un error bloqueante en la base de datos: {str(e)}', 'danger')
        
    return redirect(url_for('inventory_bp.index'))

@inventory_bp.route('/producto/<int:id>/agregar_variante', methods=['POST'])
@login_required
@admin_or_bodega_required
def agregar_variante(id):
    producto = Product.query.get_or_404(id)
    nombre_variante = request.form.get('nombre_variante')
    cantidad_stock = int(request.form.get('cantidad_stock', 0))
    
    precio_costo_req = request.form.get('precio_costo')
    precio_minimo_req = request.form.get('precio_minimo')
    precio_sugerido_req = request.form.get('precio_sugerido')

    if not nombre_variante:
        flash('El nombre de la variante es obligatorio.', 'danger')
        return redirect(url_for('inventory_bp.index'))

    nueva_variante = ProductVariant(
        product_id=producto.id,
        nombre_variante=nombre_variante,
        cantidad_stock=cantidad_stock,
        precio_costo=float(precio_costo_req) if precio_costo_req else producto.precio_costo,
        precio_minimo=float(precio_minimo_req) if precio_minimo_req else producto.precio_minimo,
        precio_sugerido=float(precio_sugerido_req) if precio_sugerido_req else producto.precio_sugerido
    )
    try:
        db.session.add(nueva_variante)
        
        # Forzar el stock base a 0 para que el sistema calcule el valor usando solo las variantes
        producto.cantidad_stock = 0
        
        if cantidad_stock > 0:
            ajuste = StockAdjustment(
                product_id=producto.id,
                admin_id=current_user.id,
                tipo_movimiento=f'Creación de Subcategoría: {nombre_variante}',
                stock_anterior=0,
                stock_nuevo=cantidad_stock
            )
            db.session.add(ajuste)
            
        db.session.commit()
        flash(f'Variante "{nombre_variante}" agregada con éxito.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error al agregar la variante.', 'danger')

    return redirect(url_for('inventory_bp.index'))

@inventory_bp.route('/variante/<int:id>/editar', methods=['POST'])
@login_required
@admin_or_bodega_required
def editar_variante(id):
    variante = ProductVariant.query.get_or_404(id)
    
    variante.nombre_variante = request.form.get('nombre_variante')
    stock_anterior = variante.cantidad_stock
    
    cantidad_stock_req = request.form.get('cantidad_stock')
    cantidad_sumar = request.form.get('cantidad_sumar')
    
    if cantidad_sumar and int(cantidad_sumar) != 0:
        variante.cantidad_stock += int(cantidad_sumar)
    elif cantidad_stock_req is not None:
        variante.cantidad_stock = int(cantidad_stock_req)
    
    precio_costo_req = request.form.get('precio_costo')
    precio_minimo_req = request.form.get('precio_minimo')
    precio_sugerido_req = request.form.get('precio_sugerido')
    
    if precio_costo_req: variante.precio_costo = float(precio_costo_req)
    if precio_minimo_req: variante.precio_minimo = float(precio_minimo_req)
    if precio_sugerido_req: variante.precio_sugerido = float(precio_sugerido_req)
    
    try:
        if stock_anterior != variante.cantidad_stock:
            ajuste = StockAdjustment(
                product_id=variante.product_id,
                admin_id=current_user.id,
                tipo_movimiento=f'Edición de stock Subcategoría: {variante.nombre_variante}',
                stock_anterior=stock_anterior,
                stock_nuevo=variante.cantidad_stock
            )
            db.session.add(ajuste)

        db.session.commit()
        flash('Variante editada con éxito.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error al editar la variante.', 'danger')
        
    return redirect(url_for('inventory_bp.index'))

@inventory_bp.route('/variantes_masivo/<int:producto_id>', methods=['POST'])
@login_required
@admin_or_bodega_required
def editar_variantes_masivo(producto_id):
    v_ids = request.form.getlist('v_id[]')
    nombres = request.form.getlist('nombre_variante[]')
    sumas = request.form.getlist('cantidad_sumar[]')
    costos = request.form.getlist('precio_costo[]')
    minimos = request.form.getlist('precio_minimo[]')
    sugs = request.form.getlist('precio_sugerido[]')
    
    cambios_realizados = 0

    for i, vid_str in enumerate(v_ids):
        try:
            vid = int(vid_str)
            variante = ProductVariant.query.get(vid)
            if not variante or variante.product_id != producto_id:
                continue
                
            # Actualizar nombres si cambiaron
            if nombres[i].strip() and variante.nombre_variante != nombres[i].strip():
                variante.nombre_variante = nombres[i].strip()
                cambios_realizados += 1
            
            # Sumar stock
            stock_anterior = variante.cantidad_stock
            if sumas[i] and int(sumas[i]) != 0:
                variante.cantidad_stock += int(sumas[i])
                ajuste = StockAdjustment(
                    product_id=variante.product_id,
                    admin_id=current_user.id,
                    tipo_movimiento=f'Edición masiva de stock Subcategoría: {variante.nombre_variante}',
                    stock_anterior=stock_anterior,
                    stock_nuevo=variante.cantidad_stock
                )
                db.session.add(ajuste)
                cambios_realizados += 1
                
            # Precios
            if costos[i] and float(costos[i]) != variante.precio_costo:
                variante.precio_costo = float(costos[i])
                cambios_realizados += 1
            if minimos[i] and float(minimos[i]) != variante.precio_minimo:
                variante.precio_minimo = float(minimos[i])
                cambios_realizados += 1
            if sugs[i] and float(sugs[i]) != variante.precio_sugerido:
                variante.precio_sugerido = float(sugs[i])
                cambios_realizados += 1

        except Exception as e:
            continue
            
    if cambios_realizados > 0:
        try:
            db.session.commit()
            flash('Subcategorías actualizadas masivamente con éxito.', 'success')
        except Exception as e:
            db.session.rollback()
            flash('Error al guardar los cambios masivos.', 'danger')
    else:
        flash('No se detectaron cambios en las subcategorías.', 'info')
        
    return redirect(url_for('inventory_bp.index'))

@inventory_bp.route('/variante/<int:id>/eliminar', methods=['POST'])
@login_required
@admin_or_bodega_required
def eliminar_variante(id):
    variante = ProductVariant.query.get_or_404(id)
    
    from models import SaleDetail
    # Validar si ya hay ventas facturadas con esta variante para evitar conflictos en el Balance Financiero
    if SaleDetail.query.filter_by(variant_id=variante.id).first():
        flash('Acción denegada: No se puede eliminar una variante que tiene ventas facturadas (por integridad financiera). Sugerencia: Actualiza su stock a 0.', 'warning')
        return redirect(url_for('inventory_bp.index'))
        
    try:
        nombre = variante.nombre_variante
        product_id = variante.product_id
        stock_anterior = variante.cantidad_stock
        
        db.session.delete(variante)
        
        if stock_anterior > 0:
            ajuste = StockAdjustment(
                product_id=product_id,
                admin_id=current_user.id,
                tipo_movimiento=f'Eliminación de Subcategoría: {nombre}',
                stock_anterior=stock_anterior,
                stock_nuevo=0
            )
            db.session.add(ajuste)
            
        db.session.commit()
        flash(f'La subcategoría "{nombre}" fue borrada exitosamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error grave en servidor al eliminar la variante: {str(e)}', 'danger')
        
    return redirect(url_for('inventory_bp.index'))

@inventory_bp.route('/plantilla-importacion')
@login_required
@admin_or_bodega_required
def descargar_plantilla():
    # Crear un DataFrame de estructura requerida
    df = pd.DataFrame(columns=['sku', 'nombre', 'subcategoria', 'cantidad_stock', 'precio_costo', 'precio_minimo', 'precio_sugerido', 'observacion'])
    
    # Filas de ejemplo para guiar al usuario
    df.loc[0] = ['SKU-EJEMPLO-01', 'Audífonos Bluetooth Inalambricos', '', 50, 10000, 14000, 20000, 'Producto sencillo sin variantes']
    df.loc[1] = ['SKU-EJEMPLO-02', 'Cargador Original Carga Rápida', 'Color Negro', 100, 5000, 7500, 12000, 'Ejemplo de Variante/Subcategoría']
    df.loc[2] = ['SKU-EJEMPLO-02', 'Cargador Original Carga Rápida', 'Color Blanco', 30, 5000, 7500, 12000, 'Se agrupará al mismo SKU']
    
    output = BytesIO()
    df.to_excel(output, index=False, engine='openpyxl')
    output.seek(0)
    
    return send_file(output, download_name="plantilla_importacion.xlsx", as_attachment=True)

@inventory_bp.route('/importar', methods=['POST'])
@login_required
@admin_or_bodega_required
def importar_inventario():
    if 'archivo' not in request.files:
        flash('No se seleccionó ningún archivo.', 'danger')
        return redirect(url_for('inventory_bp.index'))
        
    archivo = request.files['archivo']
    if archivo.filename == '':
        flash('Ningún archivo seleccionado.', 'danger')
        return redirect(url_for('inventory_bp.index'))
        
    if not (archivo.filename.endswith('.xlsx') or archivo.filename.endswith('.csv')):
        flash('Formato no válido. Solo debes subir archivos .xlsx o .csv', 'warning')
        return redirect(url_for('inventory_bp.index'))
        
    try:
        # Lectura con pandas según la extensión
        if archivo.filename.endswith('.csv'):
            df = pd.read_csv(archivo)
        else:
            df = pd.read_excel(archivo)
            
        required_cols = ['sku', 'nombre', 'cantidad_stock', 'precio_costo', 'precio_minimo', 'precio_sugerido', 'observacion']
        
        # Limpieza de encabezados para evitar problemas por mayúsculas o espacios accidentales
        df.columns = [str(c).strip().lower() for c in df.columns]
        
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            flash(f"El archivo rechazado. Faltan las siguientes columnas: {', '.join(missing)}", 'danger')
            return redirect(url_for('inventory_bp.index'))
            
        if 'subcategoria' not in df.columns:
            df['subcategoria'] = ''
            
        tipo = 'bodega' if current_user.rol == 'bodega' else 'tienda'
        creados = 0
        actualizados = 0
        
        for idx, row in df.iterrows():
            sku_raw = str(row['sku']).strip()
            if not sku_raw or sku_raw.lower() == 'nan':
                continue
                
            # Limpiar cantidades para evitar errores NaN o Nulls
            cant = int(row['cantidad_stock']) if pd.notna(row['cantidad_stock']) else 0
            costo = float(row['precio_costo']) if pd.notna(row['precio_costo']) else 0.0
            minimo = float(row['precio_minimo']) if pd.notna(row['precio_minimo']) else 0.0
            sugerido = float(row['precio_sugerido']) if pd.notna(row['precio_sugerido']) else 0.0
            nombre_val = str(row['nombre']).strip()
            
            subcat_val = str(row['subcategoria']).strip() if 'subcategoria' in row and pd.notna(row['subcategoria']) else ''
            if subcat_val.lower() == 'nan': subcat_val = ''
            
            obs_val = str(row['observacion']).strip() if pd.notna(row['observacion']) else ''
            if obs_val.lower() == 'nan':
                obs_val = ''

            prod = Product.query.filter_by(sku=sku_raw, tipo_inventario=tipo).first()
            
            if prod:
                # Si EXISTE el producto padre
                if subcat_val:
                    variante = ProductVariant.query.filter_by(product_id=prod.id, nombre_variante=subcat_val).first()
                    if variante:
                        # Actualizar variante existente
                        stock_anterior = variante.cantidad_stock
                        variante.cantidad_stock += cant
                        variante.precio_costo = costo
                        variante.precio_minimo = minimo
                        variante.precio_sugerido = sugerido
                        
                        if cant > 0:
                            ajuste = StockAdjustment(
                                product_id=prod.id,
                                admin_id=current_user.id,
                                tipo_movimiento=f'Ingreso Masivo Subcategoría {subcat_val}',
                                stock_anterior=stock_anterior,
                                stock_nuevo=variante.cantidad_stock
                            )
                            db.session.add(ajuste)
                        actualizados += 1
                    else:
                        # Crear nueva variante dentro del producto existente
                        nueva_variante = ProductVariant(
                            product_id=prod.id,
                            nombre_variante=subcat_val,
                            cantidad_stock=cant,
                            precio_costo=costo,
                            precio_minimo=minimo,
                            precio_sugerido=sugerido
                        )
                        db.session.add(nueva_variante)
                        # También sumar al historial para el Kardex
                        ajuste = StockAdjustment(
                            product_id=prod.id,
                            admin_id=current_user.id,
                            tipo_movimiento=f'Creación Excel Subcategoría {subcat_val}',
                            stock_anterior=0,
                            stock_nuevo=cant
                        )
                        db.session.add(ajuste)
                        creados += 1
                else:
                    # Sin variante, actualizar producto base
                    stock_anterior = prod.cantidad_stock
                    prod.cantidad_stock += cant
                    prod.precio_costo = costo
                    prod.precio_minimo = minimo
                    prod.precio_sugerido = sugerido
                    prod.nombre = nombre_val 
                    prod.observacion = obs_val
                    
                    if cant > 0:
                        ajuste = StockAdjustment(
                            product_id=prod.id,
                            admin_id=current_user.id,
                            tipo_movimiento='Suma por Ingreso Masivo (Excel)',
                            stock_anterior=stock_anterior,
                            stock_nuevo=prod.cantidad_stock
                        )
                        db.session.add(ajuste)
                    actualizados += 1
            else:
                # CREAR NUEVO PRODUCTO MAESTRO
                nuevo_prod = Product(
                    sku=sku_raw,
                    nombre=nombre_val,
                    tipo_inventario=tipo,
                    cantidad_stock=cant if not subcat_val else 0, # Si provee subcat, todo el stock se va a la subcat
                    precio_costo=costo,
                    precio_minimo=minimo,
                    precio_sugerido=sugerido,
                    observacion=obs_val
                )
                db.session.add(nuevo_prod)
                db.session.flush() # Generar ID autoincremental
                
                if subcat_val:
                    # Insertar variante atada al nuevo producto
                    nueva_variante = ProductVariant(
                        product_id=nuevo_prod.id,
                        nombre_variante=subcat_val,
                        cantidad_stock=cant,
                        precio_costo=costo,
                        precio_minimo=minimo,
                        precio_sugerido=sugerido
                    )
                    db.session.add(nueva_variante)
                    
                    ajuste = StockAdjustment(
                        product_id=nuevo_prod.id,
                        admin_id=current_user.id,
                        tipo_movimiento=f'Cr. Inicial Excel + Subcat {subcat_val}',
                        stock_anterior=0,
                        stock_nuevo=cant
                    )
                    db.session.add(ajuste)
                else:
                    ajuste = StockAdjustment(
                        product_id=nuevo_prod.id,
                        admin_id=current_user.id,
                        tipo_movimiento='Creación Inicial (Excel)',
                        stock_anterior=0,
                        stock_nuevo=nuevo_prod.cantidad_stock
                    )
                    db.session.add(ajuste)
                creados += 1
                
        db.session.commit()
        flash(f'Carga masiva completada exitosamente. Productos creados: {creados} | Agregados a stock existente: {actualizados}.', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Ocurrió un error leyendo las filas de tu archivo: {str(e)}', 'danger')
        
    return redirect(url_for('inventory_bp.index'))

@inventory_bp.route('/api/search')
@login_required
@admin_or_bodega_required
def api_search():
    query = request.args.get('q', '').strip()
    tipo = 'bodega' if current_user.rol == 'bodega' else 'tienda'
    
    if len(query) < 2:
        return jsonify([])
    
    from sqlalchemy import or_
    productos = Product.query.filter_by(tipo_inventario=tipo).filter(
        or_(
            Product.sku.ilike(f'%{query}%'),
            Product.nombre.ilike(f'%{query}%')
        )
    ).limit(10).all()
    
    results = []
    for p in productos:
        results.append({
            'id': p.id,
            'sku': p.sku,
            'nombre': p.nombre,
            'stock': p.total_stock,
            'url': url_for('inventory_bp.ver_producto', id=p.id)
        })
    
    return jsonify(results)
