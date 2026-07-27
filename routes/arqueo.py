from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, Sale, SalePayment, ArqueoCaja, Expense
from decorators import admin_required
from datetime import datetime, date
from decimal import Decimal
import re
import pytz

arqueo_bp = Blueprint('arqueo_bp', __name__)

def obtener_hora_bogota():
    return datetime.now(pytz.timezone('America/Bogota')).replace(tzinfo=None)

def calcular_totales_dia(ventas_del_dia):
    """Calcula los totales de efectivo y transferencias del día.
    Usa SalePayment si está disponible, de lo contrario usa metodo_pago legacy."""
    total_efectivo = Decimal('0')
    total_transferencia = Decimal('0')
    
    for v in ventas_del_dia:
        if v.pagos:  # Ventas nuevas con tabla sale_payments
            for pago in v.pagos:
                if pago.metodo_pago == 'efectivo':
                    total_efectivo += pago.monto
                else:  # nequi, bancolombia, daviplata, transferencia
                    total_transferencia += pago.monto
        else:  # Retrocompatibilidad con ventas antiguas
            if v.metodo_pago == 'efectivo':
                total_efectivo += v.monto_total
            elif v.metodo_pago in ['transferencia', 'nequi', 'bancolombia', 'daviplata']:
                total_transferencia += v.monto_total
    
    return total_efectivo, total_transferencia


@arqueo_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo():
    # Obtener fecha de la URL o usar hoy
    fecha_str = request.args.get('fecha', obtener_hora_bogota().strftime('%Y-%m-%d'))
    try:
        fecha_seleccionada = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        fecha_seleccionada = obtener_hora_bogota().date()
        fecha_str = fecha_seleccionada.strftime('%Y-%m-%d')

    # Calcular ventas del día usando el sistema híbrido (SalePayment + legacy)
    ventas_del_dia = Sale.query.filter(
        db.func.date(Sale.fecha_venta) == fecha_seleccionada
    ).all()
    total_efectivo, total_transferencia = calcular_totales_dia(ventas_del_dia)

    # Calcular gastos automáticos del día
    gastos_diarios_registros = Expense.query.filter(
        db.func.date(Expense.fecha_gasto) == fecha_seleccionada,
        Expense.tipo_gasto == 'Gasto Diario'
    ).all()
    gastos_automaticos = float(sum(g.monto for g in gastos_diarios_registros))

    # Calcular gastos por productos externos del día
    gastos_externos_registros = Expense.query.filter(
        db.func.date(Expense.fecha_gasto) == fecha_seleccionada,
        Expense.categoria == 'Pago Prod. Externo'
    ).all()
    gastos_externos = float(sum(g.monto for g in gastos_externos_registros))

    # Verificar si ya existe un arqueo para esa fecha
    arqueo_existente = ArqueoCaja.query.filter_by(fecha_arqueo=fecha_seleccionada).first()

    if request.method == 'POST':
        # Doble verificación en el backend para evitar duplicados por concurrencia
        if ArqueoCaja.query.filter_by(fecha_arqueo=fecha_seleccionada).first():
            flash('Ya existe un arqueo cerrado para esta fecha. No se puede duplicar.', 'warning')
            return redirect(url_for('arqueo_bp.reporte', fecha_inicio=fecha_str, fecha_fin=fecha_str))

        base_inicial = float(request.form.get('base_inicial', 0.0))
        
        # Recalcular gastos automáticos por seguridad en el backend
        gastos_recalculados = Expense.query.filter(
            db.func.date(Expense.fecha_gasto) == fecha_seleccionada,
            Expense.tipo_gasto == 'Gasto Diario'
        ).all()
        gastos_del_dia = float(sum(g.monto for g in gastos_recalculados))
        
        observaciones_gastos = request.form.get('observaciones_gastos', '').strip()

        nuevo_arqueo = ArqueoCaja(
            vendedor_id=current_user.id,
            fecha_arqueo=fecha_seleccionada,
            base_inicial=base_inicial,
            gastos_del_dia=gastos_del_dia,
            observaciones_gastos=observaciones_gastos,
            total_efectivo_sistema=total_efectivo,
            total_transferencia_sistema=total_transferencia,
            total_unidades_ch=procesar_unidades_ch(ventas_del_dia)[1]
        )

        try:
            db.session.add(nuevo_arqueo)
            db.session.commit()
            flash('Arqueo de caja guardado exitosamente.', 'success')
            return redirect(url_for('arqueo_bp.reporte', fecha_inicio=fecha_str, fecha_fin=fecha_str))
        except Exception as e:
            db.session.rollback()
            flash('Ocurrió un error al guardar el arqueo de caja.', 'danger')

    return render_template(
        'arqueo/form.html',
        fecha=fecha_str,
        total_efectivo=total_efectivo,
        total_transferencia=total_transferencia,
        arqueo_existente=arqueo_existente,
        gastos_automaticos=gastos_automaticos,
        gastos_externos=gastos_externos
    )

@arqueo_bp.route('/reporte', methods=['GET'])
@login_required
def reporte():
    fecha_inicio_str = request.args.get('fecha_inicio', obtener_hora_bogota().strftime('%Y-%m-%d'))
    fecha_fin_str = request.args.get('fecha_fin', obtener_hora_bogota().strftime('%Y-%m-%d'))

    try:
        fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
        fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
    except ValueError:
        fecha_inicio = obtener_hora_bogota().date()
        fecha_fin = obtener_hora_bogota().date()

    # BLOQUEO DE SEGURIDAD: Los vendedores no pueden ver días anteriores
    if current_user.rol != 'admin':
        hoy = obtener_hora_bogota().date()
        fecha_inicio = hoy
        fecha_fin = hoy
        fecha_inicio_str = hoy.strftime('%Y-%m-%d')
        fecha_fin_str = hoy.strftime('%Y-%m-%d')

    # Arqueo unificado: todos los usuarios ven los mismos arqueos (ya no se filtra por vendedor)
    query = ArqueoCaja.query.filter(
        ArqueoCaja.fecha_arqueo >= fecha_inicio,
        ArqueoCaja.fecha_arqueo <= fecha_fin
    )

    arqueos = query.order_by(ArqueoCaja.fecha_arqueo.desc()).all()

    # Cálculos globales para el reporte
    resumen = {
        'total_base': sum(a.base_inicial for a in arqueos),
        'total_efectivo': sum(a.total_efectivo_sistema for a in arqueos),
        'total_transferencia': sum(a.total_transferencia_sistema for a in arqueos),
        'total_gastos': sum(a.gastos_del_dia for a in arqueos)
    }
    
    
    # Calcular los gastos por productos externos en este rango de fechas
    gastos_externos_query = Expense.query.filter(
        db.func.date(Expense.fecha_gasto) >= fecha_inicio,
        db.func.date(Expense.fecha_gasto) <= fecha_fin,
        Expense.categoria == 'Pago Prod. Externo'
    ).all()
    resumen['total_gastos_externos'] = sum(g.monto for g in gastos_externos_query)
    
    resumen['total_recaudado_bruto'] = resumen['total_efectivo'] + resumen['total_transferencia']
    # Restar Celulares y TOTAL DE GASTOS de la venta neta (Los gastos externos ya están incluidos en total_gastos si se registran como Gasto Diario)
    resumen['total_recaudado_neto'] = resumen['total_recaudado_bruto'] - resumen['total_gastos']
    
    # Calcular los gastos que fueron pagados en EFECTIVO
    gastos_efectivo_query = Expense.query.filter(
        db.func.date(Expense.fecha_gasto) >= fecha_inicio,
        db.func.date(Expense.fecha_gasto) <= fecha_fin,
        Expense.tipo_gasto == 'Gasto Diario',
        Expense.metodo_pago == 'efectivo'
    ).all()
    resumen['total_gastos_efectivo'] = sum(g.monto for g in gastos_efectivo_query)

    # El efectivo esperado en caja descuenta gastos en EFECTIVO y celulares
    resumen['efectivo_esperado'] = (resumen['total_base'] + resumen['total_efectivo']) - resumen['total_gastos_efectivo']

    # Obtener todas las ventas del periodo para el detalle en la "tirilla" (unificado)
    ventas_query = Sale.query.filter(
        db.func.date(Sale.fecha_venta) >= fecha_inicio,
        db.func.date(Sale.fecha_venta) <= fecha_fin
    )
    
    ventas_periodo = ventas_query.order_by(Sale.fecha_venta.asc()).all()

    fecha_generacion = obtener_hora_bogota().strftime('%Y-%m-%d %H:%M')


    
    # Obtener todos los gastos del periodo para el reporte detallado
    gastos_periodo = Expense.query.filter(
        db.func.date(Expense.fecha_gasto) >= fecha_inicio,
        db.func.date(Expense.fecha_gasto) <= fecha_fin
    ).order_by(Expense.fecha_gasto.asc()).all()

    return render_template(
        'arqueo/reporte.html',
        arqueos=arqueos,
        resumen=resumen,
        fecha_inicio=fecha_inicio_str,
        fecha_fin=fecha_fin_str,
        fecha_generacion=fecha_generacion,
        ventas_periodo=ventas_periodo,
        gastos_periodo=gastos_periodo
    )
