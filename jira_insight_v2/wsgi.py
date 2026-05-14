import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'jira_insight_v2.settings')

application = get_wsgi_application()
