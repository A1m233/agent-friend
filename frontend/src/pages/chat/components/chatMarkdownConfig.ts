import type { ChatMessage as TdChatMessage } from "@tdesign-react/chat";
import type { ComponentProps } from "react";

type TdChatContentProps = NonNullable<ComponentProps<typeof TdChatMessage>["chatContentProps"]>;

export const CHAT_MARKDOWN_CONTENT_PROPS = {
  markdown: {
    options: {
      engine: {
        syntax: {
          autoLink: false,
        },
      },
    },
  },
} satisfies TdChatContentProps;
