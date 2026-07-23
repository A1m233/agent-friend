import { invoke } from "@tauri-apps/api/core";

export interface MigrationInventory {
  fileCount: number;
  totalBytes: number;
  sessionFileCount: number;
  attachmentFileCount: number;
  memoryFileCount: number;
  personaFileCount: number;
  userModelFileCount: number;
  managedCredentialCount: number;
  imCredentialCount: number;
  semanticMemoryCount: number;
  episodicMemoryCount: number;
  personaModified: boolean;
  hasMeaningfulData: boolean;
}

export interface MigrationManifestEntry {
  path: string;
  group: string;
  size: number;
  sha256: string;
}

export interface MigrationManifest {
  schemaVersion: number;
  sourceAppVersion: string;
  createdAt: string;
  entries: MigrationManifestEntry[];
}

export interface ExportMigrationResult {
  transferCode: string;
  outputPath: string;
  manifest: MigrationManifest;
}

export interface PreflightMigrationResult {
  operationId: string;
  manifest: MigrationManifest;
  destination: MigrationInventory;
}

export interface MigrationBackupMetadata {
  createdAt: string;
  source: string;
  size: number;
}

export interface MigrationAssetResult {
  group: string;
  status: "success" | "notPresent" | "needsUserAction" | "failed";
  itemCount: number;
  message: string;
}

export interface MigrationCommitResult {
  completedAt: string;
  assets: MigrationAssetResult[];
}

export const getMigrationInventory = () =>
  invoke<MigrationInventory>("get_migration_inventory");

export const exportMigrationPackage = (outputPath: string) =>
  invoke<ExportMigrationResult>("export_migration_package", {
    payload: { outputPath },
  });

export const preflightMigrationPackage = (
  inputPath: string,
  transferCode: string,
) =>
  invoke<PreflightMigrationResult>("preflight_migration_package", {
    payload: { inputPath, transferCode },
  });

export const preflightLatestMigrationBackup = () =>
  invoke<PreflightMigrationResult>("preflight_latest_migration_backup");

export const commitMigrationPackage = (operationId: string) =>
  invoke<MigrationCommitResult>("commit_migration_package", {
    payload: { operationId },
  });

export const getLatestMigrationBackup = () =>
  invoke<MigrationBackupMetadata | null>("get_latest_migration_backup");

export const deleteLatestMigrationBackup = () =>
  invoke<void>("delete_latest_migration_backup");

export const getLastMigrationResult = () =>
  invoke<MigrationCommitResult | null>("get_last_migration_result");

export const clearLastMigrationResult = () =>
  invoke<void>("clear_last_migration_result");
