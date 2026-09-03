from flask import Blueprint, request, render_template, current_app, flash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from models import db, ServerPayment, obtener_hora_bogota

servidor_bp = Blueprint('servidor_bp', __name__)

MESES_ESPANOL = [
    'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
    'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'
]

@servidor_bp.route('/confirmar-pago', methods=['GET', 'POST'])
def confirmar_pago():
    token = request.args.get('token') or request.form.get('token')
    if not token:
        return render_template('servidor/confirmar_pago.html', error="Token de confirmación requerido. Solicitud inválida.", status="invalid"), 400

    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        data = serializer.loads(token, salt='server-payment-salt')
        anio = data.get('anio')
        mes = data.get('mes')
        if not anio or not mes:
            raise BadSignature("Payload incompleto en el token.")
    except (BadSignature, SignatureExpired) as e:
        return render_template('servidor/confirmar_pago.html', error="El enlace de confirmación es inválido o ha expirado.", status="invalid"), 400
    except Exception as e:
        return render_template('servidor/confirmar_pago.html', error="Ocurrió un error al procesar el token de confirmación.", status="invalid"), 400

    mes_nombre = MESES_ESPANOL[mes - 1] if 1 <= mes <= 12 else str(mes)
    monto = current_app.config.get('VALOR_MENSUALIDAD_SERVIDOR', '80.000')

    # Verificar si el pago ya fue registrado en BD
    pago_existente = ServerPayment.query.filter_by(anio=anio, mes=mes, estado='pagado').first()
    if pago_existente:
        return render_template(
            'servidor/confirmar_pago.html',
            status="already_paid",
            anio=anio,
            mes_nombre=mes_nombre,
            fecha_pago=pago_existente.fecha_pago.strftime('%d/%m/%Y %I:%M %p') if pago_existente.fecha_pago else None,
            monto=monto
        )

    error_pin = None
    if request.method == 'POST':
        pin_ingresado = request.form.get('pin', '').strip()
        pin_correcto = current_app.config.get('PIN_CONFIRMACION_SERVIDOR', '9876')

        if pin_ingresado == pin_correcto:
            # Buscar o crear pago
            pago = ServerPayment.query.filter_by(anio=anio, mes=mes).first()
            if not pago:
                pago = ServerPayment(
                    anio=anio,
                    mes=mes,
                    estado='pagado',
                    fecha_pago=obtener_hora_bogota(),
                    observacion='Confirmación automática vía WhatsApp con PIN del Proveedor'
                )
                db.session.add(pago)
            else:
                pago.estado = 'pagado'
                pago.fecha_pago = obtener_hora_bogota()
                pago.observacion = 'Confirmación actualizada vía WhatsApp con PIN del Proveedor'

            db.session.commit()
            return render_template(
                'servidor/confirmar_pago.html',
                status="success",
                anio=anio,
                mes_nombre=mes_nombre,
                monto=monto,
                fecha_pago=pago.fecha_pago.strftime('%d/%m/%Y %I:%M %p')
            )
        else:
            error_pin = "🚨 PIN de confirmación incorrecto. Inténtalo nuevamente."

    return render_template(
        'servidor/confirmar_pago.html',
        status="form",
        token=token,
        anio=anio,
        mes_nombre=mes_nombre,
        monto=monto,
        error_pin=error_pin
    )
