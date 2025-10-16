"""Django settings for the forum_simulator project (iteration 1).

This configuration is intentionally minimal.  It includes only the
components required to define models and run management commands.  For the
purposes of this simulation we do not need a web interface yet, so
application-level settings such as templates and static files are left
with sensible defaults.  Adjust the `DATABASES` section to point to the
desired database backend.  By default we use SQLite for ease of setup.
"""
from __future__ import annotations

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'change-me-to-a-unique-string'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

import os

ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "boden.trexxak.com,localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("CSRF_TRUSTED_ORIGINS", "https://boden.trexxak.com").split(",") if o.strip()]

# behind nginx → tell Django the original scheme/host
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True


# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'forum',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'forum.middleware.SessionActivityMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'forum.middleware.TrexxakImpersonationMiddleware',
    'forum.middleware.APIRateLimitMiddleware',
]

ROOT_URLCONF = 'forum_simulator.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'forum.context_processors.ui_mode',
                'forum.context_processors.progress_notifications',
            ],
        },
    },
]

WSGI_APPLICATION = 'forum_simulator.wsgi.application'


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS: list[dict[str, str]] = []


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = 'static/'


# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Environment variables

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "gpt-4o-mini")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_DEFAULT_MAX_TOKENS = int(os.getenv("OPENROUTER_DEFAULT_MAX_TOKENS", "220"))
OPENROUTER_DAILY_REQUEST_LIMIT = int(os.getenv("OPENROUTER_DAILY_REQUEST_LIMIT", "1000"))
OPENROUTER_TITLE = os.getenv("OPENROUTER_TITLE")
OPENROUTER_REFERRER = os.getenv("OPENROUTER_REFERRER")

# Simulation scheduling
ENABLE_AUTO_TICKS = os.getenv('ENABLE_AUTO_TICKS', '1').lower() not in {'0', 'false', 'off'}
SIM_TICK_INTERVAL_SECONDS = int(os.getenv('SIM_TICK_INTERVAL_SECONDS', '45'))
SIM_TICK_JITTER_SECONDS = int(os.getenv('SIM_TICK_JITTER_SECONDS', '15'))
SIM_TICK_STARTUP_DELAY_SECONDS = int(os.getenv('SIM_TICK_STARTUP_DELAY_SECONDS', '8'))
SIM_TICK_QUEUE_BURST = int(os.getenv('SIM_TICK_QUEUE_BURST', '6'))

# Unlockable asset directories
PROFILE_AVATAR_BASE_URL = os.getenv(
    "PROFILE_AVATAR_BASE_URL",
    "https://imustadmitilove.trexxak.com/boden/images/profile_pictures/",
)
PROFILE_AVATAR_COUNT = int(os.getenv("PROFILE_AVATAR_COUNT", "33"))
UNLOCKABLE_AVATAR_BASE_URL = os.getenv(
    "UNLOCKABLE_AVATAR_BASE_URL",
    "https://imustadmitilove.trexxak.com/boden/images/unlockable/avatar/",
)
UNLOCKABLE_AVATAR_COUNT = int(os.getenv("UNLOCKABLE_AVATAR_COUNT", "9"))
UNLOCKABLE_EMOJI_BASE_URL = os.getenv(
    "UNLOCKABLE_EMOJI_BASE_URL",
    "https://imustadmitilove.trexxak.com/boden/images/unlockable/emoji/",
)
UNLOCKABLE_EMOJI_COUNT = int(os.getenv("UNLOCKABLE_EMOJI_COUNT", "50"))
