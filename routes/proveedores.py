import os
import time
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from models import db, Provider, ProviderInvoice, ProviderPayment, obtener_hora_bogota
from decorators import admin_required

providers_bp = Blueprint('providers_bp', __name__, url_prefix='/proveedores')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@providers_bp.route('/', methods=['GET'])
@login_required
@admin_required
def index():
    proveedores = Provider.query.order_by(Provider.nombre.asc()).all()
    # Calcular saldos para mostrarlos en la tabla
    proveedores_data = []
    for p in proveedores:
        total_facturado = sum([inv.monto_total for inv in p.facturas])
        total_abonado = sum([pay.monto_abonado for pay in p.pagos])
        saldo = total_facturado - total_abonado
        proveedores_data.append({
            'proveedor': p,
            'saldo': saldo
        })
    return render_template('admin/proveedores/index.html', proveedores_data=proveedores_data)

@providers_bp.route('/crear', methods=['POST'])
@login_required
@admin_required
def crear():
    nombre = request.form.get('nombre', '').strip()
    empresa = request.form.get('empresa', '').strip()
    telefono = request.form.get('telefono', '').strip()
    
    if not nombre:
        flash('El nombre del proveedor es obligatorio.', 'danger')
        return redirect(url_for('providers_bp.index'))
        
    nuevo_proveedor = Provider(
        nombre=nombre,
        empresa=empresa,
        telefono=telefono
    )
    db.session.add(nuevo_proveedor)
    db.session.commit()
    
    flash('Proveedor creado correctamente.', 'success')
    return redirect(url_for('providers_bp.index'))

@providers_bp.route('/<int:id>', methods=['GET'])
@login_required
@admin_required
def detalle(id):
    proveedor = Provider.query.get_or_404(id)
    
    # Calcular totales
    total_facturado = sum([inv.monto_total for inv in proveedor.facturas])
    total_abonado = sum([pay.monto_abonado for pay in proveedor.pagos])
    saldo_pendiente = total_facturado - total_abonado
    
    facturas = ProviderInvoice.query.filter_by(provider_id=id).order_by(ProviderInvoice.fecha_factura.desc()).all()
    pagos = ProviderPayment.query.filter_by(provider_id=id).order_by(ProviderPayment.fecha_pago.desc()).all()
    
    return render_template('admin/proveedores/detalle.html', 
                           proveedor=proveedor,
                           total_facturado=total_facturado,
                           total_abonado=total_abonado,
                           saldo_pendiente=saldo_pendiente,
                           facturas=facturas,
                           pagos=pagos)

@providers_bp.route('/<int:id>/invoice', methods=['POST'])
@login_required
@admin_required
def registrar_factura(id):
    proveedor = Provider.query.get_or_404(id)
    
    try:
        monto_total = float(request.form.get('monto_total', 0))
    except ValueError:
        monto_total = 0.0
        
    numero_factura = request.form.get('numero_factura', '').strip()
    descripcion = request.form.get('descripcion', '').strip()
    
    if monto_total <= 0:
        flash('El monto de la factura debe ser mayor a cero.', 'danger')
        return redirect(url_for('providers_bp.detalle', id=id))
        
    archivo = request.files.get('comprobante')
    filename_saved = None
    
    if archivo and archivo.filename != '':
        if allowed_file(archivo.filename):
            ext = archivo.filename.rsplit('.', 1)[1].lower()
            timestamp = int(time.time())
            filename_saved = f"prov_{id}_{timestamp}.{ext}"
            
            # Asegurar que existe el directorio
            providers_upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'providers')
            os.makedirs(providers_upload_folder, exist_ok=True)
            
            file_path = os.path.join(providers_upload_folder, filename_saved)
            archivo.save(file_path)
        else:
            flash('Formato de archivo no permitido (solo png, jpg, jpeg, pdf).', 'warning')
            
    nueva_factura = ProviderInvoice(
        provider_id=id,
        monto_total=monto_total,
        numero_factura=numero_factura,
        descripcion=descripcion,
        comprobante=filename_saved
    )
    
    db.session.add(nueva_factura)
    db.session.commit()
    
    flash('Factura registrada correctamente.', 'success')
    return redirect(url_for('providers_bp.detalle', id=id))

@providers_bp.route('/<int:id>/payment', methods=['POST'])
@login_required
@admin_required
def registrar_pago(id):
    proveedor = Provider.query.get_or_404(id)
    
    try:
        monto_abonado = float(request.form.get('monto_abonado', 0))
    except ValueError:
        monto_abonado = 0.0
        
    observacion = request.form.get('observacion', '').strip()
    
    if monto_abonado <= 0:
        flash('El monto del abono debe ser mayor a cero.', 'danger')
        return redirect(url_for('providers_bp.detalle', id=id))
        
    nuevo_pago = ProviderPayment(
        provider_id=id,
        monto_abonado=monto_abonado,
        observacion=observacion
    )
    
    db.session.add(nuevo_pago)
    db.session.commit()
    
    flash('Abono registrado correctamente.', 'success')
    return redirect(url_for('providers_bp.detalle', id=id))

@providers_bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar(id):
    proveedor = Provider.query.get_or_404(id)
    
    # Eliminar archivos físicos de comprobantes
    providers_upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'providers')
    for inv in proveedor.facturas:
        if inv.comprobante:
            file_path = os.path.join(providers_upload_folder, inv.comprobante)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                    
    db.session.delete(proveedor)
    db.session.commit()
    
    flash('Proveedor eliminado correctamente.', 'success')
    return redirect(url_for('providers_bp.index'))
