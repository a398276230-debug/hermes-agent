/**
 * The detail half of the Sessions master-detail layout: one session's
 * transcript, filling the main pane.
 *
 * Data flow is deliberately one-way and read-only:
 *
 *   - the initial read is a GET `/api/sessions/{id}/messages` (`read_only` on
 *     the store), and the optional live tail polls that same GET on a timer;
 *   - a poll that returns byte-identical content leaves state — and the
 *     reader's scroll position — untouched (see `transcriptSignature`);
 *   - nothing in this component writes to the store. The conversation belongs
 *     to the CLI / gateway process; this pane is a monitor.
 *
 * The parent keys this by session id, so switching sessions resets the
 * transcript, the error state and the poll cadence in one step.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, ListFilter, RefreshCw } from "lucide-react";

import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Label } from "@nous-research/ui/ui/components/label";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Switch } from "@nous-research/ui/ui/components/switch";

import { MessageList } from "@/components/SessionTranscript";
import { api, type SessionInfo, type SessionMessage } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import {
  SESSION_LIVE_TAIL_INTERVAL_MS,
  transcriptSignature,
} from "@/lib/session-live-tail";
import { sourceLabel, sourceVisual } from "@/lib/session-source";
import { cn, timeAgo } from "@/lib/utils";
import { useI18n } from "@/i18n";

interface SessionTranscriptPaneProps {
  session: SessionInfo;
  /** Auto-refresh the transcript from the read-only sessions API. */
  liveTailEnabled: boolean;
  onToggleLiveTail: (enabled: boolean) => void;
  /** The transcript changed during a live poll (row counters are stale). */
  onLiveChange: () => void;
  /** Active search term; matching bubbles are ringed and tool rows auto-expand. */
  highlight?: string;
  /** Mobile only — opens the session-list drawer. */
  onOpenList?: () => void;
  /** Whether that drawer is currently open (drives `aria-expanded`). */
  listOpen?: boolean;
  className?: string;
}

export function SessionTranscriptPane({
  session,
  liveTailEnabled,
  onToggleLiveTail,
  onLiveChange,
  highlight,
  onOpenList,
  listOpen,
  className,
}: SessionTranscriptPaneProps) {
  const { t } = useI18n();
  const [messages, setMessages] = useState<SessionMessage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [liveError, setLiveError] = useState<string | null>(null);
  // Stamp of the transcript currently on screen, so a poll that returns the
  // same content leaves state — and the reader's scroll position — untouched.
  const transcriptStampRef = useRef<string | null>(null);

  // Apply a freshly fetched transcript. Returns whether anything changed, so
  // a live poll can skip the row-metadata refresh when the agent is idle.
  const applyTranscript = useCallback((next: SessionMessage[]) => {
    const stamp = transcriptSignature(next);
    if (stamp === transcriptStampRef.current) return false;
    transcriptStampRef.current = stamp;
    setMessages(next);
    return true;
  }, []);

  // The one read this pane performs: the initial load and the error-retry
  // button share it, so they can never drift apart.
  const loadTranscript = useCallback(() => {
    let cancelled = false;
    api
      .getSessionMessages(session.id, session.profile)
      .then((resp) => {
        if (!cancelled) applyTranscript(resp.messages);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [applyTranscript, session.id, session.profile]);

  useEffect(() => loadTranscript(), [loadTranscript]);

  // Live tail: strictly read-only re-reads of the transcript. ``getSessionMessages``
  // is a GET that opens the session DB read-only on the server, so polling
  // never writes to the store and never contends for the agent's write lock.
  useEffect(() => {
    if (!liveTailEnabled) return;
    let cancelled = false;
    let inFlight = false;
    const poll = () => {
      // Skip a tick if the previous read is still outstanding — a slow or
      // hung store must not stack requests.
      if (inFlight) return;
      inFlight = true;
      api
        .getSessionMessages(session.id, session.profile)
        .then((resp) => {
          if (cancelled) return;
          setLiveError(null);
          // A successful poll also heals a failed initial read.
          setError(null);
          if (applyTranscript(resp.messages)) onLiveChange();
        })
        .catch((err) => {
          // Keep the transcript we already have: a transient read failure
          // must not blank a page the user is monitoring.
          if (!cancelled) setLiveError(errorMessage(err));
        })
        .finally(() => {
          inFlight = false;
        });
    };
    const id = setInterval(poll, SESSION_LIVE_TAIL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [
    applyTranscript,
    liveTailEnabled,
    onLiveChange,
    session.id,
    session.profile,
  ]);

  // Toggling clears the last poll failure so a stale badge cannot outlive
  // the run that produced it (an effect here would trip the
  // react-hooks/set-state-in-effect lint trap).
  const toggleLiveTail = useCallback(
    (next: boolean) => {
      setLiveError(null);
      onToggleLiveTail(next);
    },
    [onToggleLiveTail],
  );

  const liveTailId = `sessions-live-tail-${session.id}`;
  const sourceInfo = sourceVisual(session.source);
  const SourceIcon = sourceInfo.icon;
  const hasTitle = Boolean(session.title) && session.title !== "Untitled";
  const title = hasTitle
    ? session.title
    : session.preview
      ? session.preview.slice(0, 80)
      : t.sessions.untitledSession;
  const titleLabel = title ?? t.sessions.untitledSession;

  return (
    <section
      className={cn(
        "flex min-h-0 min-w-0 flex-1 flex-col border border-border bg-background-base/40",
        className,
      )}
      aria-label={t.sessions.transcript}
    >
      <header className="flex min-w-0 shrink-0 flex-col gap-2 border-b border-border px-3 py-2">
        <div className="flex min-w-0 items-start gap-2">
          {/* Mobile: the session list lives in a drawer, so the main pane
              needs its own way in. Hidden from lg up, where the list sidebar
              is permanently on screen. */}
          {onOpenList && (
            <Button
              outlined
              size="sm"
              className="shrink-0 lg:hidden"
              onClick={onOpenList}
              aria-label={t.sessions.sessionList}
              aria-controls="sessions-list-panel"
              aria-expanded={listOpen}
              aria-haspopup="dialog"
              prefix={<ListFilter />}
            >
              <span className="font-mondwest normal-case text-xs">
                {t.sessions.sessionList}
              </span>
            </Button>
          )}

          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="flex min-w-0 items-center gap-2">
              <span
                className={cn(
                  "font-mondwest min-w-0 flex-1 truncate text-sm normal-case",
                  hasTitle ? "font-medium" : "text-muted-foreground italic",
                )}
                title={titleLabel}
              >
                {titleLabel}
              </span>
              {session.is_active && (
                <Badge tone="success" className="shrink-0 text-xs">
                  <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
                  {t.common.live}
                </Badge>
              )}
            </div>

            <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-muted-foreground">
              <Badge tone="outline" className="text-xs">
                <SourceIcon className={`mr-1 h-3 w-3 ${sourceInfo.color}`} />
                {session.source ? sourceLabel(session.source) : "local"}
              </Badge>
              {session.model && (
                <>
                  <span className="max-w-[min(100%,14rem)] truncate">
                    {session.model.split("/").pop()}
                  </span>
                  <span className="text-border">&#183;</span>
                </>
              )}
              <span className="shrink-0">
                {session.message_count} {t.common.msgs}
              </span>
              {session.tool_call_count > 0 && (
                <>
                  <span className="text-border">&#183;</span>
                  <span className="shrink-0">
                    {session.tool_call_count} {t.common.tools}
                  </span>
                </>
              )}
              <span className="text-border">&#183;</span>
              <span className="shrink-0">{timeAgo(session.last_active)}</span>
            </div>
          </div>

          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Switch
              id={liveTailId}
              checked={liveTailEnabled}
              onCheckedChange={toggleLiveTail}
            />
            <Label htmlFor={liveTailId} className="cursor-pointer text-xs">
              {t.sessions.liveTail}
            </Label>
            {liveTailEnabled && (
              <Badge tone="success" className="text-xs">
                <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
                {t.common.live}
              </Badge>
            )}
          </div>
        </div>

        {liveTailEnabled && liveError && (
          <span className="flex items-center gap-1 text-xs text-destructive">
            <AlertTriangle className="h-3 w-3 shrink-0" />
            {t.sessions.liveTailFailed}
          </span>
        )}
      </header>

      {messages === null && !error && (
        <div className="flex flex-1 items-center justify-center py-8">
          <Spinner className="text-xl text-primary" />
        </div>
      )}
      {error && (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 py-8">
          <p className="text-center text-sm text-destructive">{error}</p>
          <Button
            outlined
            size="sm"
            onClick={() => {
              // A manual retry of the initial read. Same GET, no writes.
              setError(null);
              loadTranscript();
            }}
            prefix={<RefreshCw />}
          >
            <span className="font-mondwest normal-case text-xs">
              {t.common.retry}
            </span>
          </Button>
        </div>
      )}
      {!error && messages && messages.length === 0 && (
        <p className="flex-1 py-8 text-center text-sm text-muted-foreground">
          {t.sessions.noMessages}
        </p>
      )}
      {!error && messages && messages.length > 0 && (
        <MessageList
          className="flex-1 p-3"
          messages={messages}
          highlight={highlight}
          follow={liveTailEnabled}
        />
      )}
    </section>
  );
}
