from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from . import views

urlpatterns = [
    path("auth/login/", TokenObtainPairView.as_view(), name="dashboard-login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="dashboard-token-refresh"),
    path("me/", views.MeView.as_view(), name="dashboard-me"),
    path("me/password/", views.ChangePasswordView.as_view(), name="dashboard-change-password"),
    path("status/", views.StatusView.as_view(), name="dashboard-status"),
    path("options/", views.OptionsView.as_view(), name="dashboard-options"),

    path("overview/", views.OverviewView.as_view(), name="dashboard-overview"),
    path("funnel/", views.FunnelView.as_view(), name="dashboard-funnel"),
    path("loss/", views.LossView.as_view(), name="dashboard-loss"),
    path("scorecards/", views.ScorecardsView.as_view(), name="dashboard-scorecards"),
    path("leaderboards/", views.LeaderboardView.as_view(), name="dashboard-leaderboards"),

    path("deals/", views.DealListView.as_view(), name="dashboard-deals"),
    path("deals/lookup/", views.DealLookupView.as_view(), name="dashboard-deal-lookup"),
    path("deals/<str:deal_id>/", views.DealDetailView.as_view(), name="dashboard-deal-detail"),
    path("deals/<str:deal_id>/meta/", views.DealMetaView.as_view(), name="dashboard-deal-meta"),
    path("deals/<str:deal_id>/notes/", views.DealNoteView.as_view(), name="dashboard-deal-notes"),
    path("deals/<str:deal_id>/tasks/", views.DealTaskView.as_view(), name="dashboard-deal-tasks"),

    path("tasks/", views.TaskListView.as_view(), name="dashboard-tasks"),
    path("tasks/<int:task_id>/", views.TaskDetailView.as_view(), name="dashboard-task-detail"),
    path("exceptions/", views.IssueListView.as_view(), name="dashboard-exceptions"),
    path("exceptions/<int:issue_id>/", views.IssueDetailView.as_view(), name="dashboard-exception-detail"),
    path("notifications/", views.NotificationListView.as_view(), name="dashboard-notifications"),

    path("team/", views.TeamView.as_view(), name="dashboard-team"),
    path("team/<int:profile_id>/", views.TeamMemberView.as_view(), name="dashboard-team-member"),
    path("settings/", views.SettingsView.as_view(), name="dashboard-settings"),
    path("targets/", views.TargetsView.as_view(), name="dashboard-targets"),
    path("sync/", views.SyncTriggerView.as_view(), name="dashboard-sync"),
]
