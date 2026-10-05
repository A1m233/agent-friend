import type { ChatMessage as TdChatMessage } from "@tdesign-react/chat";
import type { ComponentProps } from "react";
import { markdownLinkAttributes, markdownLinkUrl } from "@/lib/links/targets";

type TdChatContentProps = NonNullable<ComponentProps<typeof TdChatMessage>["chatContentProps"]>;

export const CHAT_MARKDOWN_CONTENT_PROPS = {
  markdown: {
    options: {
      callback: { urlProcessor: markdownLinkUrl },
      engine: {
        syntax: {
          link: { attrRender: markdownLinkAttributes },
          // Cherry's flow helper fabricates <a href> from unfinished destinations.
          // Keep normal parsing (and code/table streaming) but never activate a partial path.
          aiFlowAutoClose: false,
          autoLink: false,
        },
      },
    },
  },
} satisfies TdChatContentProps;
