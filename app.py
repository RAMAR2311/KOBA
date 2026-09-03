import os
from flask import Flask, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect

# Importar la instancia de db desde models
from models import db, User

def create_app():
    app = Flask(__name__)
    
    # Configuración mediante variables de entorno
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-super-secreta')
    app.config['VALOR_MENSUALIDAD_SERVIDOR'] = os.environ.get('VALOR_MENSUALIDAD_SERVIDOR', '80.000')
    app.config['PIN_CONFIRMACION_SERVIDOR'] = os.environ.get('PIN_CONFIRMACION_SERVIDOR', '9876')
    
    # Detección inteligente de Base de Datos (PostgreSQL con Fallback automático a SQLite local)
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', 5432))
            sock.close()
            if result == 0:
                db_url = 'postgresql://postgres:admin123@localhost:5432/KOBA'
            else:
                instance_path = os.path.join(app.root_path, 'instance')
                os.makedirs(instance_path, exist_ok=True)
                db_url = f"sqlite:///{os.path.join(instance_path, 'crm_inventory.db')}"
        except Exception:
            instance_path = os.path.join(app.root_path, 'instance')
            os.makedirs(instance_path, exist_ok=True)
            db_url = f"sqlite:///{os.path.join(instance_path, 'crm_inventory.db')}"

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')


    # Inicializar Extensiones
    db.init_app(app)
    Migrate(app, db)
    CSRFProtect(app)
    
    login_manager = LoginManager()
    login_manager.login_view = 'auth_bp.login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Importar y Registrar Blueprints
    from routes.sales import sales_bp
    from routes.inventory import inventory_bp
    from routes.auth import auth_bp
    from routes.arqueo import arqueo_bp
    from routes.gastos import gastos_bp
    from routes.servidor import servidor_bp
    
    app.register_blueprint(sales_bp, url_prefix='/sales')
    app.register_blueprint(inventory_bp, url_prefix='/inventory')
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(arqueo_bp, url_prefix='/arqueo')
    app.register_blueprint(gastos_bp, url_prefix='/gastos')
    app.register_blueprint(servidor_bp, url_prefix='/servidor')
    
    # Registro de Blueprint Admin
    from routes.admin import admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # Registro de Blueprint Bodega
    from routes.bodega import bodega_bp
    app.register_blueprint(bodega_bp, url_prefix='/bodega')

    # Registro de Blueprint Proveedores
    from routes.proveedores import providers_bp
    app.register_blueprint(providers_bp, url_prefix='/proveedores')

    # Context Processor Global: Estado de Pago del Servidor
    @app.context_processor
    def inject_pago_servidor():
        import urllib.parse
        from itsdangerous import URLSafeTimedSerializer
        from flask import request
        from models import ServerPayment, obtener_hora_bogota

        MESES_ESPANOL = [
            'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
            'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'
        ]

        try:
            ahora = obtener_hora_bogota()
            anio_actual = ahora.year
            mes_actual = ahora.month
            dia_actual = ahora.day
            mes_nombre = MESES_ESPANOL[mes_actual - 1]

            pago_existente = ServerPayment.query.filter_by(
                anio=anio_actual,
                mes=mes_actual,
                estado='pagado'
            ).first()

            serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
            token = serializer.dumps({'anio': anio_actual, 'mes': mes_actual}, salt='server-payment-salt')

            try:
                url_confirmacion = url_for('servidor_bp.confirmar_pago', token=token, _external=True)
            except Exception:
                base_url = request.host_url.rstrip('/') if request else 'http://localhost:5000'
                url_confirmacion = f"{base_url}/servidor/confirmar-pago?token={token}"

            monto = app.config.get('VALOR_MENSUALIDAD_SERVIDOR', '80.000')

            mensaje_wa = (
                f"Hola, adjunto el comprobante de pago de la mensualidad del servidor Zenic (${monto} COP) para {anio_actual}.\n\n"
                f"Para confirmar mi pago en el sistema con 1 solo clic, toca aquí:\n"
                f"{url_confirmacion}"
            )

            whatsapp_url = f"https://wa.me/573115643557?text={urllib.parse.quote(mensaje_wa)}"

            # Evaluación de estado del calendario (vencimiento día 30 de cada mes)
            dias_restantes = 0
            dias_gabela = 0

            if pago_existente:
                estado = 'pagado'
            elif 1 <= dia_actual <= 21:
                estado = 'al_dia'
            elif 22 <= dia_actual <= 29:
                estado = 'preventivo'
                dias_restantes = 30 - dia_actual
            elif dia_actual == 30:
                estado = 'hoy'
                dias_restantes = 0
            elif dia_actual == 31:
                estado = 'gabela'
                dias_gabela = 5
            else:
                estado = 'vencido'

            pago_servidor = {
                'estado': estado,
                'mes_nombre': mes_nombre,
                'anio': anio_actual,
                'monto': monto,
                'dias_restantes': dias_restantes,
                'dias_gabela': dias_gabela,
                'whatsapp_url': whatsapp_url,
                'nu_llave': '@QEI910',
                'nequi_num': '3505422186'
            }
        except Exception as e:
            pago_servidor = {
                'estado': 'al_dia',
                'mes_nombre': 'Actual',
                'anio': 2026,
                'monto': '80.000',
                'dias_restantes': 0,
                'dias_gabela': 0,
                'whatsapp_url': '#',
                'nu_llave': '@QEI910',
                'nequi_num': '3505422186'
            }

        return dict(pago_servidor=pago_servidor)


    @app.template_filter('cop')
    def cop_filter(value):
        if value is None:
            return "0"
        try:
            # Formateo a moneda colombiana (separador de miles con coma, como pidió el usuario)
            return "{:,.0f}".format(float(value))
        except (ValueError, TypeError):
            return value

    @app.route('/')
    def index():
        # Redirección de sesión y rol de usuario
        if not current_user.is_authenticated:
            return redirect(url_for('auth_bp.login'))
            
        if current_user.rol == 'admin':
            return redirect(url_for('admin_bp.dashboard'))
            
        if current_user.rol == 'bodega' or current_user.rol == 'vendedor_bodega':
            return redirect(url_for('bodega_bp.dashboard'))
            
        # Por defecto, Vendedores van directo a Cajas
        return redirect(url_for('sales_bp.procesar_venta'))

    @app.route('/sw.js')
    def service_worker():
        from flask import send_from_directory
        return send_from_directory('static', 'sw.js', mimetype='application/javascript')

    return app

if __name__ == '__main__':
    app = create_app()
    
    # ---------------- LÓGICA DE INICIALIZACIÓN ----------------
    with app.app_context():
        from models import db, User
        from werkzeug.security import generate_password_hash
        
        # Aseguramos que las tablas existan sin romper migraciones
        db.create_all()
        
        # Crear la carpeta de imágenes si no existe
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        
        # Verificamos e instanciamos al Administrador si no existe
        if not User.query.filter_by(email='admin@koba.com').first():
            master_admin = User(
                nombre='Administrador Principal',
                email='admin@koba.com',
                password_hash=generate_password_hash('Admin123'),
                rol='admin' # Rol dictaminado por los requerimientos
            )
            db.session.add(master_admin)
            db.session.commit()
            print("[INFO] Usuario maestro 'admin@koba.com' fue creado automaticamente.")
            
    app.run(debug=True)
