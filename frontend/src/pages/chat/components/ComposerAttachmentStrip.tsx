import type { ReactNode } from "react";
import type { ChatTextReference } from "@/types/chat";
import { ReferenceSummaryPopover } from "./ReferenceSummaryPopover";

interface ComposerAttachmentStripProps {
  attachmentContent?: ReactNode;
  references: ChatTextReference[];
  removable: boolean;
  onReferenceRemove: (referenceId: string) => void;
  onClearReferences: () => void;
  onReferenceSelect?: (reference: ChatTextReference) => void;
}

/** Keeps text references and real files in ChatSender's single attachment band. */
export function ComposerAttachmentStrip({
  attachmentContent,
  references,
  removable,
  onReferenceRemove,
  onClearReferences,
  onReferenceSelect,
}: ComposerAttachmentStripProps) {
  if (!attachmentContent && references.length === 0) return null;

  return (
    <div slot="inner-header" className="composer-attachment-strip">
      {references.length > 0 && (
        <ReferenceSummaryPopover
          references={references}
          layout="attachment"
          removable={removable}
          onRemove={onReferenceRemove}
          onClear={onClearReferences}
          onSelect={onReferenceSelect}
        />
      )}
      {attachmentContent}
    </div>
  );
}
