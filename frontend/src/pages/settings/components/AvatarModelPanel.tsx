import { useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { emit } from "@tauri-apps/api/event";
import { FolderOpen } from "lucide-react";
import {
  Badge,
  Button,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Slider,
} from "@/components/ui";
import {
  DEFAULT_AVATAR_MODEL_ID,
  resolveInstalledAvatarModelSelection,
  useInstalledAvatarModelCatalog,
  useUserAvatarModelScan,
} from "@/avatar-models";
import {
  AVATAR_MODEL_SCALE_MAX_PERCENT,
  AVATAR_MODEL_SCALE_MIN_PERCENT,
  AVATAR_SCALE_STEP_PERCENT,
  getAvatarModelScalePercent,
  PET_MINI_SCALE_MAX_PERCENT,
  PET_MINI_SCALE_MIN_PERCENT,
  useSetting,
} from "@/lib/settings";
import { SettingsGroup } from "./SettingsGroup";
import { SettingsPageLayout } from "./SettingsPageLayout";
import { SettingsRow } from "./SettingsRow";
import { createAvatarScalePreviewScheduler } from "./avatarScalePreview";

function backendLabel(backend: string): string {
  return backend === "live2d" ? "Live2D" : "Spine";
}

export function AvatarModelPanel() {
  const [avatarModelId, setAvatarModelId] = useSetting("avatarModelId");
  const [avatarModelScalePercents, setAvatarModelScalePercents] = useSetting(
    "avatarModelScalePercents",
  );
  const [petMiniScalePercent, setPetMiniScalePercent] = useSetting(
    "petMiniScalePercent",
  );
  const { entries, loading, error } = useInstalledAvatarModelCatalog();
  const scan = useUserAvatarModelScan();
  const selectedEntry = resolveInstalledAvatarModelSelection(entries, avatarModelId);
  const value = selectedEntry?.id ?? DEFAULT_AVATAR_MODEL_ID;
  const persistedModelScale = getAvatarModelScalePercent(
    avatarModelScalePercents,
    value,
  );
  const [modelScale, setModelScale] = useState(persistedModelScale);
  const [miniScale, setMiniScale] = useState(petMiniScalePercent);
  const previewScheduler = useMemo(
    () =>
      createAvatarScalePreviewScheduler((payload) => {
        void emit("avatar-models://scale-preview", payload);
      }),
    [],
  );

  useEffect(() => setModelScale(persistedModelScale), [persistedModelScale]);
  useEffect(() => setMiniScale(petMiniScalePercent), [petMiniScalePercent]);
  useEffect(() => () => previewScheduler.dispose(), [previewScheduler]);

  const previewScale = previewScheduler.queue;

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
        <SettingsRow
          label="模型大小"
          tooltip="每个模型单独保存。拖动时桌宠会实时预览，松开后保存。"
        >
          <div className="flex w-64 items-center gap-3">
            <Slider
              min={AVATAR_MODEL_SCALE_MIN_PERCENT}
              max={AVATAR_MODEL_SCALE_MAX_PERCENT}
              step={AVATAR_SCALE_STEP_PERCENT}
              value={[modelScale]}
              aria-label="模型大小"
              onValueChange={([next]) => {
                setModelScale(next);
                previewScale({ modelId: value, modelScalePercent: next });
              }}
              onValueCommit={([next]) => {
                previewScheduler.flush();
                void setAvatarModelScalePercents({
                  ...avatarModelScalePercents,
                  [value]: next,
                });
              }}
            />
            <span className="w-12 text-right text-sm tabular-nums text-muted">
              {modelScale}%
            </span>
          </div>
        </SettingsRow>
        <SettingsRow
          label="缩小模式比例"
          tooltip="进入缩小模式时，在当前模型大小基础上应用的全局比例。"
        >
          <div className="flex w-64 items-center gap-3">
            <Slider
              min={PET_MINI_SCALE_MIN_PERCENT}
              max={PET_MINI_SCALE_MAX_PERCENT}
              step={AVATAR_SCALE_STEP_PERCENT}
              value={[miniScale]}
              aria-label="缩小模式比例"
              onValueChange={([next]) => {
                setMiniScale(next);
                previewScale({ miniScalePercent: next });
              }}
              onValueCommit={([next]) => {
                previewScheduler.flush();
                void setPetMiniScalePercent(next);
              }}
            />
            <span className="w-12 text-right text-sm tabular-nums text-muted">
              {miniScale}%
            </span>
          </div>
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup title="导入 Spine 3.8 模型">
        <div className="text-sm leading-6 text-muted">
          打开受管理目录，把一个完整模型文件夹拖进去。应用会自动发现并校验同名的
          .skel、.atlas 和 atlas 引用的贴图，无需手动刷新。
        </div>
        <Button
          type="button"
          variant="outline"
          className="self-start"
          onClick={() => void invoke("open_user_spine38_models_directory")}
        >
          <FolderOpen />
          打开 Spine 3.8 模型目录
        </Button>
        {scan.candidates.length > 0 ? (
          <div className="text-sm text-muted">
            已发现 {scan.candidates.length} 个模型；新模型通过 runtime 校验后会自动出现在上方列表。
          </div>
        ) : null}
        {scan.diagnostics.map((diagnostic) => (
          <div
            key={`${diagnostic.modelDir}:${diagnostic.code}`}
            className="rounded-md border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger"
          >
            {diagnostic.message}
          </div>
        ))}
        {error ? <div className="text-sm text-danger">{error}</div> : null}
      </SettingsGroup>
    </SettingsPageLayout>
  );
}
