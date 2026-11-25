from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "GMAO GBS DC"
admin.site.site_title = "GMAO GBS DC"
admin.site.index_title = "Administration de la maintenance"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("maintenance.urls")),
]