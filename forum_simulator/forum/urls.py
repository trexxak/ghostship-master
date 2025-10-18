from __future__ import annotations

from django.urls import path
from django.views.generic import RedirectView, TemplateView

from . import views, api

app_name = "forum"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("robots.txt", TemplateView.as_view(template_name="forum/robots.txt", content_type="text/plain"), name="robots"),
    path("service-worker.js", TemplateView.as_view(
            template_name="pwa/service-worker.js",
               content_type="application/javascript"
    ), name="service_worker"),
    path("manifest.webmanifest", TemplateView.as_view(
            template_name="pwa/manifest.webmanifest",
               content_type="application/manifest+json"
    ), name="manifest"),
    path("threads/<int:pk>/", views.thread_detail, name="thread_detail"),
    path("posts/<int:pk>/report/", views.report_post, name="report_post"),
    path("boards/new/", views.create_board, name="board_create"),
    path("boards/new", views.create_board),
    path("boards/<slug:parent_slug>/new/", views.create_board, name="subboard_create"),
    path("boards/<slug:parent_slug>/new", views.create_board),
    path("boards/", views.board_list, name="board_list"),
    path("boards/<slug:slug>/", views.board_detail, name="board_detail"),
    path("moderation/", views.moderation_dashboard, name="moderation_dashboard"),
    path("moderation/tickets/<int:pk>/action/", views.moderation_ticket_action, name="moderation_ticket_action"),
    path("who/", views.presence_who, name="who"),
    path("agents/", views.agent_list, name="agent_list"),
    path("agents/<int:pk>/", views.agent_detail, name="agent_detail"),
    path("oracle/", views.oracle_log, name="oracle_log"),
    path("raw-outputs/", views.raw_outputs, name="raw_outputs"),
    path("ticks/<int:tick_number>/", views.tick_detail, name="tick_detail"),
    path("oi/connect/", views.oi_connect, name="oi_connect"),
    path("oi/disconnect/", views.oi_disconnect, name="oi_disconnect"),
    path("oi/manual/", views.oi_manual_entry, name="oi_manual_entry"),
    path("oi/panel/", views.oi_control_panel, name="oi_control_panel"),
    path("oi/messages/", views.oi_messages, name="oi_messages"),
    path("oi/debug/role/", views.oi_set_debug_role, name="oi_set_debug_role"),
    path("oi/tools/posts/<int:pk>/visibility/", views.oi_toggle_post_visibility, name="oi_post_visibility"),
    path("oi/tools/threads/<int:pk>/visibility/", views.oi_toggle_thread_visibility, name="oi_thread_visibility"),
    path("oi/tools/threads/<int:pk>/lock/", views.oi_toggle_thread_lock, name="oi_thread_lock"),
    path("oi/tools/threads/<int:pk>/pin/", views.oi_toggle_thread_pin, name="oi_thread_pin"),
    path("oi/tools/boards/<int:pk>/visibility/", views.oi_toggle_board_visibility, name="oi_board_visibility"),
    # New friendly URL for the landing page. Keep the old path as a redirect.
    path("blog/", views.mission_board, name="mission_board"),
    path("missions/", RedirectView.as_view(pattern_name="forum:mission_board", permanent=False)),
    path("admin/hygiene/", views.data_hygiene, name="data_hygiene"),
    path("api/notifications/", api.api_notifications, name="api_notifications"),
    path("api/ticks/", api.api_tick_list, name="api_tick_list"),
    path("api/ticks/<int:tick_number>/",
         api.api_tick_detail, name="api_tick_detail"),
    path("api/oracle/", api.api_oracle_list, name="api_oracle_list"),
    path("api/oracle/ticks/", api.api_oracle_ticks, name="api_oracle_ticks"),
    path("api/boards/", api.api_board_list, name="api_board_list"),
    path("api/boards/<slug:slug>/", api.api_board_detail, name="api_board_detail"),
    path("api/agents/", api.api_agent_list, name="api_agent_list"),
    path("api/agents/<int:pk>/", api.api_agent_detail, name="api_agent_detail"),
    path("api/threads/", api.api_thread_list, name="api_thread_list"),
    path("api/threads/<int:pk>/", api.api_thread_detail, name="api_thread_detail"),
    path("api/threads/<int:pk>/updates/",
         api.api_thread_updates, name="api_thread_updates"),
    path("api/preview/", views.preview_post, name="preview_post"),
    path("api/mailboxes/<int:pk>/", api.api_mailbox, name="api_mailbox"),
    path("api/ghosts/<int:pk>/dm-mirror/",
         api.api_agent_dm_mirror, name="api_agent_dm_mirror"),
    path("dm/compose/<int:recipient_id>/", views.compose_dm, name="compose_dm"),
    path("oi/tools/moderation/tickets/<int:pk>/action/", views.oi_ticket_action, name="oi_ticket_action"),
    path("oi/tools/moderation/tickets/<int:pk>/scrap/", views.oi_scrap_ticket, name="oi_ticket_scrap"),
    path("oi/tools/moderation/tickets/<int:pk>/resolve/", views.oi_resolve_ticket, name="oi_ticket_resolve"),
]
