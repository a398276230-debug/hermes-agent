/**
 * The *master* half of the Sessions master-detail layout: search box,
 * scrollable list of session rows, and the bulk-selection toolbar.
 *
 * Rendered twice, from one implementation:
 *
 *   - as the permanent right-hand sidebar on desktop (>= lg, via CSS), and
 *   - inside the mobile drawer (`BottomSheet`) below lg, opened from the
 *     transcript header.
 *
 * Nothing here fetches or writes; the page owns the data, the filters and the
 * selection so both renderings always agree.
 */
import type { ReactNode } from "react";
import { Clock, Search, X } from "lucide-react";

import { Button } from "@nous-research/ui/ui/components/button";
import { Input } from "@nous-research/ui/ui/components/input";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

import { SessionListRow } from "@/components/SessionListRow";
import type { SessionInfo } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useI18n } from "@/i18n";

interface SessionListPanelProps {
  /** Transcript currently on screen in the main pane. */
  activeId: string | null;
  className?: string;
  /** Empty-state headline when the visible list has no rows. */
  emptyMessage: string;
  /** Optional secondary line under `emptyMessage` (e.g. "start a conversation"). */
  emptyHint?: string;
  /** Rendered below the rows (compact pagination in the mobile drawer). */
  footer?: ReactNode;
  onDelete: (id: string) => void;
  onExport: (id: string) => void;
  onRename: (id: string, title: string) => Promise<void>;
  onRowCheckClick: (
    event: React.MouseEvent,
    index: number,
    visibleList: SessionInfo[],
  ) => void;
  onSearchChange: (value: string) => void;
  onSelect: (id: string) => void;
  resumeInChatEnabled: boolean;
  search: string;
  searching: boolean;
  /** Bulk-selection toolbar, rendered above the list. */
  selectionBar?: ReactNode;
  selectedIds: Set<string>;
  sessions: SessionInfo[];
  snippetMap: Map<string, string>;
}

export function SessionListPanel({
  activeId,
  className,
  emptyMessage,
  emptyHint,
  footer,
  onDelete,
  onExport,
  onRename,
  onRowCheckClick,
  onSearchChange,
  onSelect,
  resumeInChatEnabled,
  search,
  searching,
  selectionBar,
  selectedIds,
  sessions,
  snippetMap,
}: SessionListPanelProps) {
  const { t } = useI18n();

  return (
    <div
      className={cn(
        "flex min-h-0 min-w-0 flex-col border border-border bg-background-base/40",
        className,
      )}
    >
      <div className="relative shrink-0 border-b border-border p-2">
        {searching ? (
          <Spinner className="absolute left-4 top-1/2 -translate-y-1/2 text-[0.875rem] text-primary" />
        ) : (
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
        )}
        <Input
          placeholder={t.sessions.searchPlaceholder}
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          className="h-8 py-0 pr-7 pl-8 text-xs leading-none"
        />
        {search && (
          <Button
            ghost
            size="xs"
            className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            onClick={() => onSearchChange("")}
            aria-label={t.common.clear}
          >
            <X />
          </Button>
        )}
      </div>

      {selectionBar}

      {sessions.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-1 px-4 py-10 text-center text-muted-foreground">
          <Clock className="mb-2 h-8 w-8 opacity-40" />
          <p className="text-sm font-medium">{emptyMessage}</p>
          {emptyHint && (
            <p className="text-xs text-text-tertiary">{emptyHint}</p>
          )}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto p-2">
          {sessions.map((s, index) => (
            <SessionListRow
              key={s.id}
              session={s}
              snippet={snippetMap.get(s.id)}
              isActive={activeId === s.id}
              isChecked={selectedIds.has(s.id)}
              onCheckClick={(event) => onRowCheckClick(event, index, sessions)}
              onSelect={() => onSelect(s.id)}
              onDelete={() => onDelete(s.id)}
              onRename={onRename}
              onExport={onExport}
              resumeInChatEnabled={resumeInChatEnabled}
            />
          ))}
        </div>
      )}

      {footer && (
        <div className="shrink-0 border-t border-border px-2 py-2">{footer}</div>
      )}
    </div>
  );
}
