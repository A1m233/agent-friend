import {
  Badge,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";
import {
  DEFAULT_AVATAR_MODEL_ID,
  resolveInstalledAvatarModelSelection,
  useInstalledAvatarModelCatalog,
} from "@/avatar-models";
import { useSetting } from "@/lib/settings";
import { SettingsGroup } from "./SettingsGroup";
import { SettingsPageLayout } from "./SettingsPageLayout";
import { SettingsRow } from "./SettingsRow";

function backendLabel(backend: string): string {
  return backend === "live2d" ? "Live2D" : "Spine";
}

export function AvatarModelPanel() {
  const [avatarModelId, setAvatarModelId] = useSetting("avatarModelId");
  const { entries, loading, error } = useInstalledAvatarModelCatalog();
  const selectedEntry = resolveInstalledAvatarModelSelection(entries, avatarModelId);
  const value = selectedEntry?.id ?? DEFAULT_AVATAR_MODEL_ID;

  return (
    <SettingsPageLayout title="桌宠">
      <SettingsGroup title="形象">
        <SettingsRow
          label="当前模型"
          tooltip="模型资源安装在用户数据目录，切换后只影响桌宠窗口。"
          tooltipAriaLabel="当前模型说明"
        >
          <Select
            value={value}
            disabled={loading || entries.length === 0}
            onValueChange={(next) => void setAvatarModelId(next)}
          >
            <SelectTrigger className="min-w-56">
              <SelectValue placeholder={loading ? "加载中" : "选择模型"} />
            </SelectTrigger>
            <SelectContent>
              {entries.map((entry) => (
                <SelectItem key={entry.id} value={entry.id}>
                  {entry.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </SettingsRow>
        {selectedEntry ? (
          <SettingsRow label="运行时">
            <Badge variant="secondary">{backendLabel(selectedEntry.backend)}</Badge>
          </SettingsRow>
        ) : null}
        {error ? <div className="text-sm text-danger">{error}</div> : null}
      </SettingsGroup>
    </SettingsPageLayout>
  );
}
