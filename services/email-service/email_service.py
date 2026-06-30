#!/usr/bin/env python3
"""
Nekazari Email Service
=====================

Servicio de email profesional para la plataforma Nekazari.
Maneja envío de emails de bienvenida, recuperación de contraseñas,
notificaciones del sistema y comunicaciones con farmers.

Características:
- Configuración dinámica desde variables de entorno
- Templates HTML profesionales
- Soporte para múltiples tipos de email
- Integración con SMTP seguro
- Logging completo
- Health checks
"""

import os
import sys
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

_common = os.path.join(os.path.dirname(__file__), "..", "common")
if _common not in sys.path:
    sys.path.insert(0, _common)
from api_errors import internal_error  # noqa: E402

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Inicializar Flask app
app = Flask(__name__)
_cors_origins = [o.strip() for o in os.getenv('CORS_ORIGINS', 'http://localhost:3000,http://localhost:5173').split(',') if o.strip()]
CORS(app, origins=_cors_origins, supports_credentials=True)

class EmailConfig:
    """Configuración del servicio de email desde variables de entorno"""
    
    def __init__(self):
        self.smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
        self.smtp_port = int(os.getenv('SMTP_PORT', '587'))
        self.smtp_username = os.getenv('SMTP_USERNAME') or os.getenv('SMTP_USER', '')
        self.smtp_password = os.getenv('SMTP_PASSWORD', '')
        self.smtp_tls = os.getenv('SMTP_TLS', 'true').lower() == 'true'
        self.from_email = os.getenv('SMTP_FROM_EMAIL', self.smtp_username)
        self.from_name = os.getenv('SMTP_FROM_NAME', 'Nekazari Platform')
        # Get frontend URL, constructing from PRODUCTION_DOMAIN if not set
        try:
            from common.config_manager import ConfigManager
            self.frontend_url = ConfigManager.get_frontend_url()
        except ImportError:
            # Fallback if config_manager not available
            self.frontend_url = os.getenv('FRONTEND_URL', '').rstrip('/')
        self.keycloak_url = os.getenv('KEYCLOAK_URL', 'http://keycloak-service:8080')
        
        # Validar configuración crítica
        if not self.smtp_username or not self.smtp_password:
            logger.warning("SMTP credentials not configured. Email service will be disabled.")
            self.enabled = False
        else:
            self.enabled = True

class EmailTemplates:
    """Templates HTML profesionales para emails"""
    
    @staticmethod
    def get_base_template():
        """Template base con estilos profesionales"""
        return """
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{{ title }}</title>
            <style>
                body {
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                    background-color: #f4f4f4;
                }
                .container {
                    background-color: #ffffff;
                    padding: 30px;
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }
                .header {
                    text-align: center;
                    border-bottom: 3px solid #2ecc71;
                    padding-bottom: 20px;
                    margin-bottom: 30px;
                }
                .logo {
                    font-size: 28px;
                    font-weight: bold;
                    color: #2ecc71;
                    margin-bottom: 10px;
                }
                .content {
                    margin-bottom: 30px;
                }
                .button {
                    display: inline-block;
                    padding: 12px 30px;
                    background-color: #2ecc71;
                    color: white;
                    text-decoration: none;
                    border-radius: 5px;
                    font-weight: bold;
                    margin: 20px 0;
                }
                .button:hover {
                    background-color: #27ae60;
                }
                .footer {
                    text-align: center;
                    font-size: 12px;
                    color: #666;
                    border-top: 1px solid #eee;
                    padding-top: 20px;
                    margin-top: 30px;
                }
                .highlight {
                    background-color: #f8f9fa;
                    padding: 15px;
                    border-left: 4px solid #2ecc71;
                    margin: 20px 0;
                }
                .warning {
                    background-color: #fff3cd;
                    padding: 15px;
                    border-left: 4px solid #ffc107;
                    margin: 20px 0;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div class="logo">🌱 Nekazari</div>
                    <p>Plataforma Agrícola Inteligente</p>
                </div>
                <div class="content">
                    {{ content }}
                </div>
                <div class="footer">
                    <p>© 2024 Nekazari Platform. Todos los derechos reservados.</p>
                    <p>Este es un email automático, por favor no respondas directamente.</p>
                </div>
            </div>
        </body>
        </html>
        """
    
    @staticmethod
    def welcome_email(farmer_name: str, farm_name: str, tenant_id: str, api_key: str, frontend_url: str):
        """Template para email de bienvenida con API key"""
        content = f"""
        <h2>¡Bienvenido a Nekazari, {farmer_name}!</h2>
        
        <p>Nos complace darte la bienvenida a la plataforma agrícola más avanzada. Tu cuenta ha sido creada exitosamente para la granja <strong>{farm_name}</strong>.</p>
        
        <div class="highlight">
            <h3>🔑 Tu API Key</h3>
            <p>Esta es tu clave de API única para conectar tus sensores y dispositivos:</p>
            <code style="background-color: #f8f9fa; padding: 10px; display: block; font-family: monospace; word-break: break-all;">{api_key}</code>
            <p><strong>⚠️ Importante:</strong> Guarda esta clave de forma segura. Solo se mostrará una vez.</p>
        </div>
        
        <h3>🚀 Próximos pasos:</h3>
        <ul>
            <li>Configura tus sensores usando la API key proporcionada</li>
            <li>Explora el dashboard en <a href="{frontend_url}">{frontend_url}</a></li>
            <li>Consulta la documentación de la API</li>
            <li>Configura alertas y notificaciones</li>
        </ul>
        
        <p>Si tienes alguna pregunta, no dudes en contactarnos.</p>
        
        <p>¡Que tengas una excelente experiencia con Nekazari!</p>
        """
        return EmailTemplates.get_base_template().replace('{{ title }}', 'Bienvenido a Nekazari').replace('{{ content }}', content)
    
    @staticmethod
    def password_reset_email(farmer_name: str, reset_token: str, frontend_url: str):
        """Template para recuperación de contraseña"""
        reset_url = f"{frontend_url}/reset-password?token={reset_token}"
        content = f"""
        <h2>Recuperación de Contraseña</h2>
        
        <p>Hola {farmer_name},</p>
        
        <p>Hemos recibido una solicitud para restablecer la contraseña de tu cuenta en Nekazari.</p>
        
        <div class="warning">
            <p><strong>Si no solicitaste este cambio, ignora este email.</strong></p>
        </div>
        
        <p>Para restablecer tu contraseña, haz clic en el siguiente enlace:</p>
        
        <p style="text-align: center;">
            <a href="{reset_url}" class="button">Restablecer Contraseña</a>
        </p>
        
        <p>O copia y pega este enlace en tu navegador:</p>
        <p style="word-break: break-all; background-color: #f8f9fa; padding: 10px;">{reset_url}</p>
        
        <p><strong>Este enlace expirará en 1 hora por seguridad.</strong></p>
        """
        return EmailTemplates.get_base_template().replace('{{ title }}', 'Recuperación de Contraseña').replace('{{ content }}', content)
    
    @staticmethod
    def notification_email(farmer_name: str, notification_type: str, message: str, frontend_url: str):
        """Template para notificaciones del sistema"""
        content = f"""
        <h2>Notificación del Sistema</h2>
        
        <p>Hola {farmer_name},</p>
        
        <div class="highlight">
            <h3>📢 {notification_type}</h3>
            <p>{message}</p>
        </div>
        
        <p>Puedes ver más detalles en tu <a href="{frontend_url}">dashboard de Nekazari</a>.</p>
        """
        return EmailTemplates.get_base_template().replace('{{ title }}', 'Notificación Nekazari').replace('{{ content }}', content)
    
    @staticmethod
    def activation_email(farmer_name: str, activation_code: str, frontend_url: str):
        """Template para activación de cuenta"""
        activation_url = f"{frontend_url}/activate?code={activation_code}"
        content = f"""
        <h2>Activa tu Cuenta</h2>
        
        <p>Hola {farmer_name},</p>
        
        <p>Tu cuenta en Nekazari está lista para ser activada.</p>
        
        <div class="highlight">
            <h3>🔐 Código de Activación</h3>
            <p>Usa este código para activar tu cuenta:</p>
            <code style="background-color: #f8f9fa; padding: 10px; display: block; font-family: monospace; font-size: 18px; text-align: center;">{activation_code}</code>
        </div>
        
        <p style="text-align: center;">
            <a href="{activation_url}" class="button">Activar Cuenta</a>
        </p>
        
        <p>O visita <a href="{frontend_url}/activate">{frontend_url}/activate</a> e introduce el código manualmente.</p>
        """
        return EmailTemplates.get_base_template().replace('{{ title }}', 'Activa tu Cuenta').replace('{{ content }}', content)
    
    @staticmethod
    def expiration_email(farmer_name: str, days_remaining: int, expires_at: str, plan: str, frontend_url: str):
        """Template para notificación de expiración de código"""
        urgency_color = "#dc2626" if days_remaining <= 7 else "#f59e0b" if days_remaining <= 15 else "#3b82f6"
        urgency_text = "URGENTE" if days_remaining <= 7 else "IMPORTANTE" if days_remaining <= 15 else "AVISO"
        
        if days_remaining == 1:
            urgency_message = "¡Tu plan expira MAÑANA!"
        elif days_remaining <= 7:
            urgency_message = f"¡Tu plan expira en {days_remaining} días!"
        else:
            urgency_message = f"Tu plan expira en {days_remaining} días"
        
        content = f"""
        <h2>⚠️ Aviso de Expiración</h2>
        
        <p>Hola {farmer_name},</p>
        
        <div class="highlight" style="border-left: 4px solid {urgency_color}; background-color: #fef3c7;">
            <h3 style="color: {urgency_color};">{urgency_text}</h3>
            <p style="font-size: 18px; font-weight: bold; color: {urgency_color};">{urgency_message}</p>
        </div>
        
        <p>Tu plan <strong>{plan.upper()}</strong> está próximo a expirar:</p>
        
        <ul style="background-color: #f8f9fa; padding: 15px; border-radius: 5px;">
            <li><strong>Días restantes:</strong> {days_remaining} día{'s' if days_remaining != 1 else ''}</li>
            <li><strong>Fecha de expiración:</strong> {expires_at}</li>
            <li><strong>Plan actual:</strong> {plan.upper()}</li>
        </ul>
        
        <p>Para renovar tu plan y continuar disfrutando de todos los servicios de Nekazari:</p>
        
        <p style="text-align: center;">
            <a href="{frontend_url}/settings" class="button" style="background-color: {urgency_color};">Renovar Plan</a>
        </p>
        
        <p>Si tienes alguna pregunta, no dudes en contactarnos.</p>
        """
        return EmailTemplates.get_base_template().replace('{{ title }}', f'Aviso de Expiración - {days_remaining} días').replace('{{ content }}', content)
    
    @staticmethod
    def activation_success_notification(user_email: str, tenant_id: str, tenant_name: str, plan: str, activation_code: str, frontend_url: str):
        """Template para notificación de registro exitoso a administradores"""
        content = f"""
        <h2>✅ Nuevo Tenant Registrado Exitosamente</h2>
        
        <p>Se ha completado exitosamente el registro de un nuevo tenant en la plataforma Nekazari.</p>
        
        <div class="highlight">
            <h3>📋 Detalles del Registro</h3>
            <ul style="list-style: none; padding: 0;">
                <li><strong>Email del usuario:</strong> {user_email}</li>
                <li><strong>Tenant ID:</strong> {tenant_id}</li>
                <li><strong>Nombre del tenant:</strong> {tenant_name}</li>
                <li><strong>Plan:</strong> {plan.upper()}</li>
                <li><strong>Código de activación:</strong> {activation_code}</li>
                <li><strong>Fecha:</strong> {datetime.utcnow().strftime('%d/%m/%Y %H:%M UTC')}</li>
            </ul>
        </div>
        
        <p>El tenant está completamente operativo y el usuario puede acceder al dashboard.</p>
        
        <p style="text-align: center;">
            <a href="{frontend_url}/admin/tenants" class="button">Ver Tenants</a>
        </p>
        """
        return EmailTemplates.get_base_template().replace('{{ title }}', 'Nuevo Tenant Registrado').replace('{{ content }}', content)
    
    @staticmethod
    def activation_failure_notification(user_email: str, tenant_name: str, activation_code: str, error_reason: str, frontend_url: str):
        """Template para notificación de fallo en registro"""
        content = f"""
        <h2>⚠️ Error en Registro de Tenant</h2>
        
        <p>Se ha producido un error durante el proceso de registro de un nuevo tenant.</p>
        
        <div class="warning">
            <h3>❌ Detalles del Error</h3>
            <ul style="list-style: none; padding: 0;">
                <li><strong>Email del usuario:</strong> {user_email}</li>
                <li><strong>Nombre del tenant:</strong> {tenant_name}</li>
                <li><strong>Código de activación:</strong> {activation_code}</li>
                <li><strong>Fecha:</strong> {datetime.utcnow().strftime('%d/%m/%Y %H:%M UTC')}</li>
            </ul>
        </div>
        
        <div class="highlight" style="border-left: 4px solid #dc2626; background-color: #fee2e2;">
            <h3 style="color: #dc2626;">Razón del Error</h3>
            <p style="color: #991b1b; font-family: monospace; background-color: #fef2f2; padding: 10px; border-radius: 5px;">{error_reason}</p>
        </div>
        
        <p><strong>Acción requerida:</strong> Revisar los logs del sistema y corregir el problema. El código de activación ha sido marcado como no usado y puede ser reintentado.</p>
        
        <p style="text-align: center;">
            <a href="{frontend_url}/admin/logs" class="button" style="background-color: #dc2626;">Revisar Logs</a>
        </p>
        """
        return EmailTemplates.get_base_template().replace('{{ title }}', 'Error en Registro de Tenant').replace('{{ content }}', content)
    
    @staticmethod
    def invitation_email(invitee_name: str, inviter_name: str, tenant_name: str, role: str, invitation_code: str, invitation_url: str, expires_at: str, frontend_url: str):
        """Template para invitación de usuario a tenant"""
        from datetime import datetime
        try:
            expires_date = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            expires_str = expires_date.strftime('%d/%m/%Y a las %H:%M')
        except Exception:
            expires_str = expires_at
        
        role_names = {
            'Farmer': 'Agricultor',
            'DeviceManager': 'Gestor de Dispositivos',
            'TechnicalConsultant': 'Consultor Técnico'
        }
        role_display = role_names.get(role, role)
        
        content = f"""
        <h2>🎉 ¡Has sido invitado a unirte a Nekazari!</h2>
        
        <p>Hola {invitee_name or 'Usuario'},</p>
        
        <p><strong>{inviter_name}</strong> te ha invitado a unirte al tenant <strong>{tenant_name}</strong> en la plataforma Nekazari con el rol de <strong>{role_display}</strong>.</p>
        
        <div class="highlight">
            <h3>📋 Detalles de la Invitación</h3>
            <ul style="list-style: none; padding: 0;">
                <li><strong>Tenant:</strong> {tenant_name}</li>
                <li><strong>Rol asignado:</strong> {role_display}</li>
                <li><strong>Invitado por:</strong> {inviter_name}</li>
                <li><strong>Válido hasta:</strong> {expires_str}</li>
            </ul>
        </div>
        
        <p>Para aceptar la invitación y crear tu cuenta, haz clic en el siguiente botón:</p>
        
        <p style="text-align: center;">
            <a href="{invitation_url}" class="button">Aceptar Invitación</a>
        </p>
        
        <p>O visita <a href="{frontend_url}/accept-invitation?code={invitation_code}">{frontend_url}/accept-invitation?code={invitation_code}</a> e introduce el código manualmente.</p>
        
        <div class="warning">
            <p><strong>Código de Invitación:</strong></p>
            <code style="background-color: #f8f9fa; padding: 10px; display: block; font-family: monospace; font-size: 18px; text-align: center;">{invitation_code}</code>
            <p style="margin-top: 10px;"><strong>⚠️ Importante:</strong> Este código expirará el {expires_str}. Asegúrate de crear tu cuenta antes de esa fecha.</p>
        </div>
        
        <p>Si no esperabas esta invitación, puedes ignorar este email.</p>
        
        <p>¡Esperamos verte pronto en Nekazari!</p>
        """
        return EmailTemplates.get_base_template().replace('{{ title }}', 'Invitación a Nekazari').replace('{{ content }}', content)

class EmailService:
    """Servicio principal de envío de emails"""
    
    def __init__(self, config: EmailConfig):
        self.config = config
        self.templates = EmailTemplates()
        self.templates_dir = os.path.join(os.path.dirname(__file__), 'templates')
    
    def send_email(self, to_email: str, subject: str, html_content: str, text_content: str = None) -> bool:
        """Envía un email usando SMTP"""
        if not self.config.enabled:
            logger.warning("Email service is disabled. Skipping email send.")
            return False
        
        try:
            # Crear mensaje
            msg = MIMEMultipart('alternative')
            msg['From'] = self.config.from_email
            msg['To'] = to_email
            msg['Subject'] = subject
            
            # Agregar contenido HTML
            html_part = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(html_part)
            
            # Agregar contenido texto plano si se proporciona
            if text_content:
                text_part = MIMEText(text_content, 'plain', 'utf-8')
                msg.attach(text_part)
            
            # Conectar y enviar
            # Use SMTP_SSL for port 465, SMTP with starttls for port 587
            if self.config.smtp_port == 465:
                server = smtplib.SMTP_SSL(self.config.smtp_host, self.config.smtp_port)
            else:
                server = smtplib.SMTP(self.config.smtp_host, self.config.smtp_port)
                if self.config.smtp_tls:
                    server.starttls()
            
            server.login(self.config.smtp_username, self.config.smtp_password)
            server.sendmail(self.config.from_email, [to_email], msg.as_string())
            server.quit()
            
            logger.info(f"Email sent successfully to {to_email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False
    
    def send_welcome_email(self, email: str, farmer_name: str, farm_name: str, tenant_id: str, api_key: str) -> bool:
        """Envía email de bienvenida con API key"""
        html_content = self.templates.welcome_email(farmer_name, farm_name, tenant_id, api_key, self.config.frontend_url)
        subject = f"¡Bienvenido a Nekazari, {farmer_name}!"
        return self.send_email(email, subject, html_content)
    
    def send_password_reset_email(self, email: str, farmer_name: str, reset_token: str) -> bool:
        """Envía email de recuperación de contraseña"""
        html_content = self.templates.password_reset_email(farmer_name, reset_token, self.config.frontend_url)
        subject = "Recuperación de Contraseña - Nekazari"
        return self.send_email(email, subject, html_content)
    
    def send_notification_email(self, email: str, farmer_name: str, notification_type: str, message: str) -> bool:
        """Envía email de notificación del sistema"""
        html_content = self.templates.notification_email(farmer_name, notification_type, message, self.config.frontend_url)
        subject = f"Notificación Nekazari - {notification_type}"
        return self.send_email(email, subject, html_content)
    
    def send_activation_email(self, email: str, farmer_name: str, activation_code: str) -> bool:
        """Envía email de activación de cuenta"""
        html_content = self.templates.activation_email(farmer_name, activation_code, self.config.frontend_url)
        subject = "Activa tu Cuenta - Nekazari"
        return self.send_email(email, subject, html_content)
    
    def send_expiration_email(self, email: str, farmer_name: str, days_remaining: int, expires_at: str, plan: str, tenant: str = None) -> bool:
        """Envía email de notificación de expiración"""
        html_content = self.templates.expiration_email(farmer_name, days_remaining, expires_at, plan, self.config.frontend_url)
        subject = f"⚠️ Tu plan Nekazari expira en {days_remaining} día{'s' if days_remaining != 1 else ''}"
        return self.send_email(email, subject, html_content)
    
    def send_invitation_email(self, email: str, inviter_name: str, tenant_name: str, role: str, invitation_code: str, invitation_url: str, expires_at: str) -> bool:
        """Envía email de invitación a usuario"""
        html_content = self.templates.invitation_email(
            invitee_name=email.split('@')[0],
            inviter_name=inviter_name,
            tenant_name=tenant_name,
            role=role,
            invitation_code=invitation_code,
            invitation_url=invitation_url,
            expires_at=expires_at,
            frontend_url=self.config.frontend_url
        )
        subject = f"🎉 Invitación para unirte a {tenant_name} en Nekazari"
        return self.send_email(email, subject, html_content)
    
    def send_verification_otp_email(self, email: str, otp: str) -> bool:
        """Send an OTP verification email to the user."""
        try:
            with open(os.path.join(self.templates_dir, "verification_otp.html"), "r", encoding='utf-8') as f:
                template = f.read()

            html_content = template.format(
                OTP=otp,
                YEAR=datetime.now().year,
                NKZ_URL="https://nekazari.robotika.cloud"
            )

            text_content = f"Tu código de verificación de Nekazari es: {otp}\nEste código caducará en 15 minutos.\nSi no has solicitado este código, ignora este correo."
            return self.send_email(email, "Verifica tu correo - Nekazari", html_content, text_content)
        except Exception as e:
            logger.error(f"Error sending OTP verification email: {e}")
            return False

    def send_activation_success_notification(self, user_email: str, tenant_id: str, tenant_name: str, plan: str, activation_code: str, platform_email: str, tenant_admin_email: str) -> bool:
        """Envía notificación de registro exitoso a administradores"""
        html_content = self.templates.activation_success_notification(
            user_email=user_email,
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            plan=plan,
            activation_code=activation_code,
            frontend_url=self.config.frontend_url
        )
        subject = f"✅ Nuevo Tenant Registrado: {tenant_name}"
        
        # Enviar a ambos emails
        success_platform = self.send_email(platform_email, subject, html_content)
        success_admin = self.send_email(tenant_admin_email, subject, html_content)
        
        return success_platform and success_admin
    
    def send_activation_failure_notification(self, user_email: str, tenant_name: str, activation_code: str, error_reason: str, platform_email: str) -> bool:
        """Envía notificación de fallo en registro a administradores"""
        html_content = self.templates.activation_failure_notification(
            user_email=user_email,
            tenant_name=tenant_name,
            activation_code=activation_code,
            error_reason=error_reason,
            frontend_url=self.config.frontend_url
        )
        subject = f"⚠️ Error en Registro de Tenant: {tenant_name}"
        
        # Enviar solo al email de la plataforma (el usuario aún no tiene tenant admin)
        return self.send_email(platform_email, subject, html_content)

# Inicializar servicios
config = EmailConfig()
email_service = EmailService(config)

# =============================================================================
# ENDPOINTS REST API
# =============================================================================

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    status = {
        'status': 'healthy' if config.enabled else 'disabled',
        'service': 'email-service',
        'timestamp': datetime.utcnow().isoformat(),
        'smtp_configured': bool(config.smtp_username and config.smtp_password),
        'version': '1.0.0'
    }
    return jsonify(status), 200 if config.enabled else 503

@app.route('/send/welcome', methods=['POST'])
def send_welcome():
    """Endpoint para enviar email de bienvenida"""
    try:
        data = request.get_json()
        
        # Validar datos requeridos
        required_fields = ['email', 'farmer_name', 'farm_name', 'tenant_id', 'api_key']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Enviar email
        success = email_service.send_welcome_email(
            email=data['email'],
            farmer_name=data['farmer_name'],
            farm_name=data['farm_name'],
            tenant_id=data['tenant_id'],
            api_key=data['api_key']
        )
        
        if success:
            return jsonify({'message': 'Welcome email sent successfully'}), 200
        else:
            return jsonify({'error': 'Failed to send welcome email'}), 500
            
    except Exception as e:
        logger.error(f"Error in send_welcome endpoint: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/send/password-reset', methods=['POST'])
def send_password_reset():
    """Endpoint para enviar email de recuperación de contraseña"""
    try:
        data = request.get_json()
        
        # Validar datos requeridos
        required_fields = ['email', 'farmer_name']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Usar reset_url si se proporciona, sino generar con reset_token
        reset_token = data.get('reset_token', 'KEYCLOAK_RESET')
        reset_url = data.get('reset_url')
        
        if not reset_url:
            # Si no hay URL específica, usar la URL de Keycloak reset
            reset_url = f"{email_service.config.frontend_url}/auth/realms/nekazari/login-actions/reset-credentials?client_id=nekazari-frontend"
        
        # Modificar el template para usar la URL directa
        html_content = email_service.templates.password_reset_email(
            data['farmer_name'],
            reset_token,
            email_service.config.frontend_url
        )
        
        # Reemplazar el token placeholder con la URL real si se proporciona
        if reset_url and reset_url != reset_token:
            html_content = html_content.replace(
                f"{email_service.config.frontend_url}/reset-password?token={reset_token}",
                reset_url
            )
        
        subject = "Recuperación de Contraseña - Nekazari"
        success = email_service.send_email(data['email'], subject, html_content)
        
        if success:
            return jsonify({'message': 'Password reset email sent successfully'}), 200
        else:
            return jsonify({'error': 'Failed to send password reset email'}), 500
            
    except Exception as e:
        logger.error(f"Error in send_password_reset endpoint: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/send/notification', methods=['POST'])
def send_notification():
    """Endpoint para enviar email de notificación"""
    try:
        data = request.get_json()
        
        # Validar datos requeridos
        required_fields = ['email', 'farmer_name', 'notification_type', 'message']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Enviar email
        success = email_service.send_notification_email(
            email=data['email'],
            farmer_name=data['farmer_name'],
            notification_type=data['notification_type'],
            message=data['message']
        )
        
        if success:
            return jsonify({'message': 'Notification email sent successfully'}), 200
        else:
            return jsonify({'error': 'Failed to send notification email'}), 500
            
    except Exception as e:
        logger.error(f"Error in send_notification endpoint: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/send/activation', methods=['POST'])
def send_activation():
    """Endpoint para enviar email de activación"""
    try:
        data = request.get_json()
        
        # Validar datos requeridos
        required_fields = ['email', 'farmer_name', 'activation_code']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Enviar email
        success = email_service.send_activation_email(
            email=data['email'],
            farmer_name=data['farmer_name'],
            activation_code=data['activation_code']
        )
        
        if success:
            return jsonify({'message': 'Activation email sent successfully'}), 200
        else:
            return jsonify({'error': 'Failed to send activation email'}), 500
            
    except Exception as e:
        logger.error(f"Error in send_activation endpoint: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/send/expiration', methods=['POST'])
def send_expiration():
    """Endpoint para enviar email de notificación de expiración"""
    try:
        data = request.get_json()
        
        # Validar datos requeridos
        required_fields = ['email', 'farmer_name', 'days_remaining', 'expires_at', 'plan']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Enviar email
        success = email_service.send_expiration_email(
            email=data['email'],
            farmer_name=data['farmer_name'],
            days_remaining=int(data['days_remaining']),
            expires_at=data['expires_at'],
            plan=data['plan'],
            tenant=data.get('tenant')
        )
        
        if success:
            return jsonify({'message': 'Expiration notification email sent successfully'}), 200
        else:
            return jsonify({'error': 'Failed to send expiration notification email'}), 500
            
    except Exception as e:
        logger.error(f"Error in send_expiration endpoint: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/send/invitation', methods=['POST'])
def send_invitation():
    """Endpoint para enviar email de invitación a usuario"""
    try:
        data = request.get_json()
        
        # Validar datos requeridos
        required_fields = ['email', 'inviter_name', 'tenant_name', 'role', 'invitation_code', 'invitation_url', 'expires_at']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Enviar email
        success = email_service.send_invitation_email(
            email=data['email'],
            inviter_name=data['inviter_name'],
            tenant_name=data['tenant_name'],
            role=data['role'],
            invitation_code=data['invitation_code'],
            invitation_url=data['invitation_url'],
            expires_at=data['expires_at']
        )
        
        if success:
            return jsonify({'message': 'Invitation email sent successfully'}), 200
        else:
            return jsonify({'error': 'Failed to send invitation email'}), 500
            
    except Exception as e:
        logger.error(f"Error in send_invitation endpoint: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/email/verification-otp', methods=['POST'])
def send_verification_otp():
    """Send an OTP verification email"""
    data = request.json
    email = data.get('email')
    otp = data.get('otp')

    if not all([email, otp]):
        return jsonify({'error': 'Email and OTP are required'}), 400

    try:
        success = email_service.send_verification_otp_email(
            email=email,
            otp=otp
        )
        if success:
            return jsonify({'message': 'OTP email sent successfully'}), 200
        else:
            return jsonify({'error': 'Failed to send email'}), 500
    except Exception as e:
        logger.error(f"Error in send_verification_otp endpoint: {e}")
        return internal_error(e, 'email_send_verification_otp')

@app.route('/email/activation-success', methods=['POST'])
def send_activation_success():
    """Endpoint para enviar notificación de registro exitoso"""
    try:
        data = request.get_json()
        
        # Validar datos requeridos
        required_fields = ['user_email', 'tenant_id', 'tenant_name', 'plan', 'activation_code', 'platform_email', 'tenant_admin_email']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Enviar notificaciones
        success = email_service.send_activation_success_notification(
            user_email=data['user_email'],
            tenant_id=data['tenant_id'],
            tenant_name=data['tenant_name'],
            plan=data['plan'],
            activation_code=data['activation_code'],
            platform_email=data['platform_email'],
            tenant_admin_email=data['tenant_admin_email']
        )
        
        if success:
            return jsonify({'message': 'Activation success notifications sent successfully'}), 200
        else:
            return jsonify({'error': 'Failed to send activation success notifications'}), 500
            
    except Exception as e:
        logger.error(f"Error in send_activation_success endpoint: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/send/activation-failure', methods=['POST'])
def send_activation_failure():
    """Endpoint para enviar notificación de fallo en registro"""
    try:
        data = request.get_json()
        
        # Validar datos requeridos
        required_fields = ['user_email', 'tenant_name', 'activation_code', 'error_reason', 'platform_email']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Enviar notificación
        success = email_service.send_activation_failure_notification(
            user_email=data['user_email'],
            tenant_name=data['tenant_name'],
            activation_code=data['activation_code'],
            error_reason=data['error_reason'],
            platform_email=data['platform_email']
        )
        
        if success:
            return jsonify({'message': 'Activation failure notification sent successfully'}), 200
        else:
            return jsonify({'error': 'Failed to send activation failure notification'}), 500
            
    except Exception as e:
        logger.error(f"Error in send_activation_failure endpoint: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/test', methods=['POST'])
def test_email():
    """Endpoint para probar el envío de emails"""
    try:
        data = request.get_json()
        
        if 'email' not in data:
            return jsonify({'error': 'Missing required field: email'}), 400
        
        # Enviar email de prueba
        test_content = """
        <h2>Email de Prueba</h2>
        <p>Este es un email de prueba del servicio de Nekazari.</p>
        <p>Si recibes este email, el servicio está funcionando correctamente.</p>
        <p>Timestamp: {}</p>
        """.format(datetime.utcnow().isoformat())
        
        success = email_service.send_email(
            to_email=data['email'],
            subject="Prueba de Email - Nekazari",
            html_content=test_content
        )
        
        if success:
            return jsonify({'message': 'Test email sent successfully'}), 200
        else:
            return jsonify({'error': 'Failed to send test email'}), 500
            
    except Exception as e:
        logger.error(f"Error in test_email endpoint: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/config', methods=['GET'])
def get_config():
    """Endpoint para obtener configuración (sin credenciales)"""
    config_info = {
        'enabled': config.enabled,
        'smtp_host': config.smtp_host,
        'smtp_port': config.smtp_port,
        'smtp_tls': config.smtp_tls,
        'from_email': config.from_email,
        'from_name': config.from_name,
        'frontend_url': config.frontend_url,
        'credentials_configured': bool(config.smtp_username and config.smtp_password)
    }
    return jsonify(config_info), 200

# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    logger.info("Starting Nekazari Email Service...")
    logger.info(f"SMTP Host: {config.smtp_host}:{config.smtp_port}")
    logger.info(f"From: {config.from_name} <{config.from_email}>")
    logger.info(f"Service enabled: {config.enabled}")
    
    app.run(host='0.0.0.0', port=5000, debug=False)
