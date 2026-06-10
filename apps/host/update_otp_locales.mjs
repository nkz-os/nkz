import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const localesDir = path.join(__dirname, 'public/locales');
const locales = ['es', 'en', 'ca', 'eu', 'fr', 'pt'];

const newKeys = {
  es: {
    otp_title: 'Verifica tu correo electrónico',
    otp_message: 'Hemos enviado un código de 6 dígitos a',
    otp_instruction: 'Introdúcelo a continuación para crear tu cuenta.',
    otp_label: 'Código de verificación',
    verifying: 'Verificando...',
    confirm_otp: 'Confirmar y Crear Cuenta',
    back_to_email: 'Volver y corregir email',
  },
  en: {
    otp_title: 'Verify your email address',
    otp_message: 'We have sent a 6-digit code to',
    otp_instruction: 'Enter it below to create your account.',
    otp_label: 'Verification code',
    verifying: 'Verifying...',
    confirm_otp: 'Confirm and Create Account',
    back_to_email: 'Go back and fix email',
  }
};

// Use English as fallback for missing translations in other languages
locales.forEach(lang => {
  const filePath = path.join(localesDir, lang, 'common.json');
  if (fs.existsSync(filePath)) {
    const data = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    
    if (!data.registration) {
      data.registration = {};
    }
    
    const translations = newKeys[lang] || newKeys['en'];
    
    Object.assign(data.registration, translations);
    
    fs.writeFileSync(filePath, JSON.stringify(data, null, 2) + '\n');
    console.log(`Updated ${lang}/common.json`);
  }
});
