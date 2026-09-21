/**
 * One row of the session list (desktop sidebar and mobile drawer).
 *
 * This is the *master* half of the master-detail layout, so the row is dense
 * by design: clicking anywhere on it swaps the transcript in the main pane
 * instead of expanding an inline accordion. Per-row actions (resume, rename,
 * export, delete) live on the row itself; the bulk-select checkbox stays an
 * independent gesture so selecting rows for deletion never navigates.
 */
import { useCallback, useState } from "react";
import { useNavigate } from "react-router";
import {
  Check,
  Download,
  Pencil,
  Play,
  Trash2,
  X,
} from "lucide-react";

import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Checkbox } from "@nous-research/ui/ui/components/checkbox";
import { Input } from "@nous-research/ui/ui/components/input";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

import type { SessionInfo } from "@/lib/api";
import { sourceLabel, sourceVisual } from "@/lib/session-source";
import { cn, timeAgo } from "@/lib/utils";
import { useI18n } from "@/i18n";

/** Render an FTS5 snippet with highlighted matches.
 *  The backend wraps matches in >>> and <<< delimiters. */
function SnippetHighlight({ snippet }: { snippet: string }) {
  const parts: React.ReactNode[] = [];
  const regex = />>>(.*?)<<</g;
  let last = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = regex.exec(snippet)) !== null) {
    if (match.index > last) {
      parts.push(snippet.slice(last, match.index));
    }
    parts.push(
      <mark key={i++} className="bg-warning/30 text-warning px-0.5">
        {match[1]}
      </mark>,
    );
    last = regex.lastIndex;
  }
  if (last < snippet.length) {
    parts.push(snippet.slice(last));
  }
  return (
    <p className="font-mondwest normal-case mt-0.5 min-w-0 max-w-full truncate text-xs text-text-secondary">
      {parts}
    </p>
  );
}

interface SessionListRowProps {
  session: SessionInfo;
  snippet?: string;
  /** This row's transcript is the one on screen in the main pane. */
  isActive: boolean;
  /** Bulk-delete checkbox state (independent of `isActive`). */
  isChecked: boolean;
  onCheckClick: (event: React.MouseEvent) => void;
  onDelete: () => void;
  onExport: (id: string) => void;
  onRename: (id: string, title: string) => Promise<void>;
  onSelect: () => void;
  resumeInChatEnabled: boolean;
}

export function SessionListRow({
  session,
  snippet,
  isActive,
  isChecked,
  onCheckClick,
  onDelete,
  onExport,
  onRename,
  onSelect,
  resumeInChatEnabled,
}: SessionListRowProps) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(session.title ?? "");
  const [renameSaving, setRenameSaving] = useState(false);

  const sourceInfo = sourceVisual(session.source);
  const SourceIcon = sourceInfo.icon;
  const hasTitle = Boolean(session.title) && session.title !== "Untitled";

  const submitRename = useCallback(async () => {
    const value = renameValue.trim();
    if (!value || value === session.title) {
      setRenaming(false);
      return;
    }
    setRenameSaving(true);
    try {
      await onRename(session.id, value);
      setRenaming(false);
    } finally {
      setRenameSaving(false);
    }
  }, [onRename, renameValue, session.id, session.title]);

  const actionButtons = (
    <>
      {resumeInChatEnabled && (
        <Button
          ghost
          size="icon"
          className="text-muted-foreground hover:text-success"
          aria-label={t.sessions.resumeInChat}
          title={t.sessions.resumeInChat}
          onClick={(e) => {
            e.stopPropagation();
            navigate(`/chat?resume=${encodeURIComponent(session.id)}`);
          }}
        >
          <Play />
        </Button>
      )}

      <Button
        ghost
        size="icon"
        className="text-muted-foreground hover:text-foreground"
        aria-label="Rename session"
        title="Rename session"
        onClick={(e) => {
          e.stopPropagation();
          setRenameValue(
            session.title && session.title !== "Untitled" ? session.title : "",
          );
          setRenaming(true);
        }}
      >
        <Pencil />
      </Button>

      <Button
        ghost
        size="icon"
        className="text-muted-foreground hover:text-foreground"
        aria-label="Export session"
        title="Export session JSON"
        onClick={(e) => {
          e.stopPropagation();
          onExport(session.id);
        }}
      >
        <Download />
      </Button>

      <Button
        ghost
        destructive
        size="icon"
        aria-label={t.sessions.deleteSession}
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
      >
        <Trash2 />
      </Button>
    </>
  );

  return (
    <div
      className={cn(
        "min-w-0 max-w-full overflow-hidden border border-l-2 transition-colors",
        isActive
          ? "border-primary/40 border-l-primary bg-primary/[0.06]"
          : session.is_active
            ? "border-border border-l-success/60 bg-success/[0.03]"
            : "border-border border-l-transparent",
      )}
    >
      <div
        className="flex cursor-pointer items-start gap-2 p-2 transition-colors hover:bg-secondary/30"
        onClick={onSelect}
      >
        {/* Clicking the checkbox must NOT switch the displayed transcript;
            selection (bulk delete) and navigation are independent gestures.
            The handler is bound on the Checkbox (a Radix ``<button>``) so
            keyboard activation takes the same path as the mouse. */}
        <span className="flex shrink-0 items-center pt-0.5">
          <Checkbox
            checked={isChecked}
            onClick={onCheckClick}
            aria-label={t.sessions.selectSession}
          />
        </span>
        <div className={cn("shrink-0 pt-0.5", sourceInfo.color)}>
          <SourceIcon className="h-4 w-4" />
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex min-w-0 flex-col gap-1.5 sm:flex-row sm:items-start sm:justify-between sm:gap-2">
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              {renaming ? (
                <div
                  className="flex min-w-0 flex-1 items-center gap-1.5"
                  onClick={(e) => e.stopPropagation()}
                >
                  <Input
                    autoFocus
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void submitRename();
                      else if (e.key === "Escape") setRenaming(false);
                    }}
                    placeholder="Session title"
                    className="h-7 min-w-0 flex-1 py-0 text-sm"
                    disabled={renameSaving}
                  />
                  <Button
                    ghost
                    size="icon"
                    className="text-muted-foreground hover:text-success"
                    aria-label="Save title"
                    title="Save title"
                    disabled={renameSaving}
                    onClick={() => void submitRename()}
                  >
                    {renameSaving ? <Spinner className="text-sm" /> : <Check />}
                  </Button>
                  <Button
                    ghost
                    size="icon"
                    className="text-muted-foreground hover:text-foreground"
                    aria-label="Cancel rename"
                    title="Cancel rename"
                    disabled={renameSaving}
                    onClick={() => setRenaming(false)}
                  >
                    <X />
                  </Button>
                </div>
              ) : (
                <div className="flex min-w-0 items-center gap-2">
                  <span
                    className={cn(
                      "font-mondwest min-w-0 flex-1 truncate text-sm normal-case",
                      hasTitle ? "font-medium" : "text-muted-foreground italic",
                    )}
                  >
                    {hasTitle
                      ? session.title
                      : session.preview
                        ? session.preview.slice(0, 60)
                        : t.sessions.untitledSession}
                  </span>
                  {session.is_active && (
                    <Badge tone="success" className="shrink-0 text-xs">
                      <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
                      {t.common.live}
                    </Badge>
                  )}
                </div>
              )}

              <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-muted-foreground">
                <Badge tone="outline" className="shrink-0 text-xs">
                  <SourceIcon className={`mr-1 h-3 w-3 ${sourceInfo.color}`} />
                  {session.source ? sourceLabel(session.source) : "local"}
                </Badge>
                {session.model && (
                  <>
                    <span className="max-w-[min(100%,10rem)] truncate">
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

              {snippet && <SnippetHighlight snippet={snippet} />}
            </div>

            {/* A single rail: it sits beside the transcript metadata from sm
                up and stacks under it on a phone (the same node, so the row
                never duplicates its buttons — duplicate aria-labels across
                two rails would double every screen-reader announcement). */}
            <div className="flex shrink-0 flex-wrap items-center gap-1">
              {actionButtons}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
