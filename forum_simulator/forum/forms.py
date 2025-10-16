from __future__ import annotations

from django import forms

from forum.models import Agent, Thread, Board


class BoardCreateForm(forms.ModelForm):
    class Meta:
        model = Board
        fields = ["name", "description"]



class PostReportForm(forms.Form):
    reporter = forms.CharField(
        label="Your ghost handle",
        max_length=64,
        required=True,
        help_text="Reports must come from a registered ghost.",
    )
    message = forms.CharField(
        label="What happened?",
        widget=forms.Textarea(attrs={"rows": 4}),
        max_length=500,
    )

    def clean_reporter(self) -> str:
        handle = (self.cleaned_data.get("reporter") or "").strip()
        if not handle:
            raise forms.ValidationError("Provide your ghost handle.")
        agent = Agent.objects.filter(name__iexact=handle).exclude(role=Agent.ROLE_BANNED).first()
        if agent is None:
            raise forms.ValidationError("Only registered ghosts can file reports.")
        self.cleaned_data["reporter_agent"] = agent
        return agent.name


class ModerationTicketActionForm(forms.Form):
    ACTION_TRIAGE = "triage"
    ACTION_START = "start"
    ACTION_RESOLVE = "resolve"
    ACTION_DISCARD = "discard"
    ACTION_ASSIGN = "assign"

    ACTION_CHOICES = [
        (ACTION_TRIAGE, "Move to triage"),
        (ACTION_START, "Mark in progress"),
        (ACTION_RESOLVE, "Resolve ticket"),
        (ACTION_DISCARD, "Discard/close"),
        (ACTION_ASSIGN, "Assign to moderator"),
    ]

    ticket_id = forms.IntegerField(widget=forms.HiddenInput)
    action = forms.ChoiceField(choices=ACTION_CHOICES)
    actor_handle = forms.CharField(
        label="Acting moderator",
        max_length=64,
        required=False,
        help_text="Defaults to t.admin when left blank.",
    )
    assignee_handle = forms.CharField(
        label="Assign to",
        max_length=64,
        required=False,
        help_text="Required when assigning a ticket.",
    )
    note = forms.CharField(
        label="Notes",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    next = forms.CharField(widget=forms.HiddenInput, required=False)

    def clean(self) -> dict[str, str]:
        cleaned = super().clean()
        action = cleaned.get("action")
        note = (cleaned.get("note") or "").strip()
        if action in {self.ACTION_RESOLVE, self.ACTION_DISCARD} and not note:
            self.add_error("note", "Please include a short note for this decision.")
        if action == self.ACTION_ASSIGN and not (cleaned.get("assignee_handle") or "").strip():
            self.add_error("assignee_handle", "Provide a moderator handle to assign to.")
        cleaned["note"] = note
        return cleaned


class AdminSettingsForm(forms.Form):
    api_daily_limit = forms.IntegerField(
        label="API daily request limit",
        min_value=100,
        help_text="Maximum number of API requests allowed per 24h window.",
    )
    thread_watch_window = forms.IntegerField(
        label="Watcher freshness window (seconds)",
        min_value=30,
        help_text="How long a user session counts as actively watching a thread.",
    )


class OrganicDraftForm(forms.Form):
    """
    Form used by the organic interface manual composer. Users can either reply to
    an existing thread, start a direct message, or spin up an entirely new
    discussion thread. The form dynamically enforces required fields based on
    the selected mode.
    """

    # Modes for the composer.  'post' replies to an existing thread,
    # 'dm' sends a private message, and 'thread' creates a brand new thread.
    MODE_POST = "post"
    MODE_DM = "dm"
    MODE_THREAD = "thread"

    MODE_CHOICES = [
        (MODE_POST, "Thread reply"),
        (MODE_DM, "Direct message"),
        (MODE_THREAD, "New thread"),
    ]

    mode = forms.ChoiceField(choices=MODE_CHOICES, initial=MODE_POST)

    # When replying to a thread, the organic operator must select which thread
    # to reply to. Only unlocked threads appear in the drop‑down, ordered by
    # recent activity.
    thread = forms.ModelChoiceField(
        queryset=Thread.objects.filter(locked=False).order_by("-last_activity_at"),
        required=False,
        label="Target thread",
    )

    # When creating a new thread the operator must choose the board where the
    # thread will live. We expose all boards ordered alphabetically. Optional
    # for other modes.
    board = forms.ModelChoiceField(
        queryset=Board.objects.order_by("name"),
        required=False,
        label="Board",
    )

    # Title for a new thread. Not used for replies or DMs.
    title = forms.CharField(
        label="Thread title",
        max_length=200,
        required=False,
    )

    # Recipient for direct messages. All non‑organic agents are available.
    recipient = forms.ModelChoiceField(
        queryset=Agent.objects.exclude(role=Agent.ROLE_ORGANIC).order_by("name"),
        required=False,
        label="DM recipient",
    )

    # Body content for any mode. Replies and new thread posts will use this as
    # the first post, while DMs simply send the content directly.
    content = forms.CharField(
        label="Message body",
        max_length=4000,
        widget=forms.Textarea(attrs={"rows": 8}),
    )

    def clean_content(self) -> str:
        """Ensure the content field is non‑empty after trimming."""
        content = (self.cleaned_data.get("content") or "").strip()
        if not content:
            raise forms.ValidationError("Provide something for trexxak to say.")
        return content

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        mode: str | None = cleaned.get("mode")

        # Required fields vary by mode:
        # - post: thread must be selected
        # - dm: recipient must be selected
        # - thread: board and title must both be provided
        if mode == self.MODE_POST:
            thread = cleaned.get("thread")
            if not thread:
                self.add_error("thread", "Select a thread to reply to.")
        elif mode == self.MODE_DM:
            recipient = cleaned.get("recipient")
            if not recipient:
                self.add_error("recipient", "Choose a ghost to DM.")
        elif mode == self.MODE_THREAD:
            board = cleaned.get("board")
            title = (cleaned.get("title") or "").strip()
            if not board:
                self.add_error("board", "Choose a board to create the thread in.")
            if not title:
                self.add_error("title", "Provide a title for the new thread.")
        return cleaned


class OrganicThreadReplyForm(forms.Form):
    content = forms.CharField(
        label="Reply",
        max_length=4000,
        widget=forms.Textarea(attrs={"rows": 8, "placeholder": "Drop your reply for the thread…"}),
    )
    quote_post_id = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def clean_content(self) -> str:
        value = (self.cleaned_data.get("content") or "").strip()
        if not value:
            raise forms.ValidationError("Type a reply before posting.")
        return value

