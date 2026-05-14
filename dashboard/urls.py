from django.urls import path
from . import views

urlpatterns = [
    path('',                        views.index,              name='index'),
    path('api/query/',              views.process_query,      name='process_query'),
    path('api/clear-session/',      views.clear_session,      name='clear_session'),
    path('api/schema/',             views.get_schema,         name='get_schema'),
    path('api/build-chart/',        views.build_chart,        name='build_chart'),
    path('api/dashboard/',          views.get_dashboard,      name='get_dashboard'),
    path('api/dashboard/add/',      views.add_widget,         name='add_widget'),
    path('api/dashboard/remove/',   views.remove_widget,      name='remove_widget'),
    path('api/dashboard/reorder/',  views.reorder_widgets,    name='reorder_widgets'),
    path('api/insight/',            views.get_insight,        name='get_insight'),
    # Dashboard view & export
    path('dashboard-view/',         views.dashboard_view,     name='dashboard_view'),
    path('api/export/pdf/',         views.export_pdf,         name='export_pdf'),
    path('api/export/pptx/',        views.export_pptx,        name='export_pptx'),
]
