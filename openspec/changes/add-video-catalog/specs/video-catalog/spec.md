## ADDED Requirements

### Requirement: Multi-Root Video Discovery
The system SHALL discover video files from configured source roots grouped by content category (EyeRecordings, WebcamRecordings, ScreenRecordings), honoring recursive flags, explicit exclude paths, and implicit `_DUP` directory exclusion.

#### Scenario: Ingest from sources config
- **WHEN** the user runs catalog ingest against `config/video_sources.yaml`
- **THEN** each existing configured root SHALL be scanned and matching video files SHALL be upserted into the SQLite catalog with category, path, and extracted metadata

#### Scenario: Skip excluded and _DUP paths
- **WHEN** a candidate file lies under an excluded directory or any path segment named `_DUP`
- **THEN** that file SHALL NOT be ingested

### Requirement: Completeness Gate
The system SHALL skip videos that appear still open for writing before upserting metadata.

#### Scenario: Incomplete CAM recording
- **WHEN** a `CAM_*` video has a timing sidecar without `wall_clock_stop_unix`, or the file is newer than the minimum age threshold
- **THEN** that file SHALL be skipped during ingest

### Requirement: Duplicate Resolution
The system SHALL group videos that share the same filename stem and duration within 1.0 second, mark one canonical keeper, and flag others as duplicates without deleting source files.

#### Scenario: Converted container pair
- **WHEN** both `Foo.mkv` and `Foo.mp4` exist with durations within 1.0s
- **THEN** the `.mkv` SHALL be marked canonical and the `.mp4` SHALL be marked duplicate with reason `converted_container`

#### Scenario: Original mp4 alone
- **WHEN** an `.mp4` has no same-stem sibling within the duration tolerance
- **THEN** that file SHALL be marked canonical (not a duplicate)

### Requirement: Consolidate Canonical Files
The system SHALL copy canonical videos into `H:\PhoBehaviorProject\RECORDINGS\{category}\` (or a configured destination root), using collision-safe destination names and recording consolidated paths in the catalog.

#### Scenario: Dry-run consolidate
- **WHEN** consolidate is run with dry-run enabled
- **THEN** the system SHALL report planned destinations without copying files

#### Scenario: Consolidate copies only keepers
- **WHEN** consolidate runs without dry-run
- **THEN** only `is_canonical` rows SHALL be copied into the matching category subfolder
