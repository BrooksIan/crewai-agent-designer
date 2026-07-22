# Workplaces: Organizing Designs by Team and Project

> **New in this release**: Workplaces are a lightweight organizational layer that groups crew designs into team namespaces, enabling better project management and team collaboration.

## Overview

A **Workplace** is a named container that groups related crew designs (crews) together. Use workplaces to:

- **Organize by team** — separate designs for different teams or departments
- **Organize by project** — group designs that contribute to a single product or initiative
- **Track membership** — optionally associate team members with a workspace
- **Navigate efficiently** — quickly switch between contexts without scrolling through hundreds of designs

## Creating a Workplace

### Via UI

1. Open the **Workplaces** tab (rightmost tab in the main view)
2. Click the **"New workplace"** tab
3. Enter:
   - **Workplace name**: identifier (letters, digits, dashes, underscores; max 64 chars)
   - **Description**: brief summary (e.g., "Q4 Agent Research Initiative")
   - **Members** (optional): comma-separated user IDs or emails
4. Click **Create**

### Via API (Python)

```python
from app import storage

# Create a workplace
workplace = storage.create_workplace(
    name="research-team",
    description="ML research group working on agent patterns"
)
```

## Assigning Designs to Workplaces

### Via UI

1. Open the **Workplaces** tab
2. Click the **"Assign to workplace"** tab
3. Select a design from the dropdown
4. Select a target workplace (or "Unassigned")
5. Click **Assign**

### Via API

```python
from app import storage

# Move a design to a workplace
storage.move_design_to_workplace(
    design_name="research-crew-v2",
    workplace_name="research-team"
)

# Remove from all workplaces (unassign)
storage.move_design_to_workplace(
    design_name="research-crew-v2",
    workplace_name=None
)
```

## Viewing Workplaces and Their Designs

### List Workplaces and Design Counts

The **Workplaces** tab shows:
- All workplaces with descriptions
- Number of designs in each
- Click "View designs" to see design names
- Quick actions: **Edit** and **Delete**

### Switch Between Workplaces

In the sidebar, under **Workplaces**:
1. Use the dropdown to select a workspace
2. The sidebar updates to show a summary (design count, description)
3. The selected workspace is tracked in session state

### Query Designs in a Workplace

```python
from app import storage

# Get all designs in a workplace
designs = storage.designs_in_workplace("research-team")
# Returns: ["research-crew-v1", "research-crew-v2", ...]
```

## Editing and Deleting Workplaces

### Edit a Workplace

1. In the **Workplaces** tab, find your workspace
2. Click the **✏️** button
3. Update **Description** or **Members**
4. Click **Save**

### Delete a Workplace

1. In the **Workplaces** tab, find your workspace
2. Click the **🗑️** button
3. Confirm the deletion

**Note**: Deleting a workplace does **not** delete its designs. Designs remain with a dangling `workspace` reference. You can safely delete empty workplaces or move designs first.

## Data Model

### Workplace (JSON Storage)

Workplaces are stored in `./workplaces/{name}.json`:

```json
{
  "name": "research-team",
  "description": "ML research group working on agent patterns",
  "created_at": "2026-07-22T13:45:30+00:00",
  "updated_at": "2026-07-22T14:12:00+00:00",
  "members": ["alice@company.com", "bob@company.com"]
}
```

### Design Updates

Designs now have an optional `workplace` field:

```json
{
  "version": 1,
  "workplace": "research-team",
  "agents": [...],
  "tasks": [...],
  "tools": [...],
  "crew": {...}
}
```

If `workplace` is `null` or omitted, the design is unassigned.

## API Reference

### `storage.create_workplace(name, description="")`

Create a new workplace.

**Returns**: `Workplace` object

**Raises**: `StorageError` if name is invalid or already exists

### `storage.load_workplace(name)`

Load a workplace by name.

**Returns**: `Workplace` object

**Raises**: `StorageError` if not found

### `storage.list_workplaces()`

List all workplace names (sorted).

**Returns**: `list[str]`

### `storage.update_workplace(name, description="", members=None)`

Update a workplace's metadata.

**Returns**: `Workplace` object

**Raises**: `StorageError` if not found

### `storage.delete_workplace(name)`

Delete a workplace metadata file. Does not delete designs.

**Raises**: `StorageError` if not found

### `storage.designs_in_workplace(workplace_name)`

List all designs assigned to a workplace.

**Returns**: `list[str]` (sorted design names)

### `storage.move_design_to_workplace(design_name, workplace_name)`

Assign a design to a workplace (or unassign if `workplace_name` is `None`).

**Raises**: `StorageError` if design or workplace not found

## Limitations and Future Work

- **No role-based access control (RBAC)**: Members are tracked but not enforced by the UI. Authorization is the responsibility of the host platform (Cloudera AI).
- **No cross-workspace search**: Designs are searched per-workspace or globally. A unified search across workplaces may be added later.
- **No workspace sharing links**: Workplaces are container metadata only; sharing is managed externally.
- **No audit trail**: Creation and update timestamps are tracked; full change history is future work.

## Examples

### Example 1: Research Team Project

```python
from app import storage

# Create a workspace for the research team
storage.create_workplace(
    "research-q4",
    description="Q4 research initiative: multi-agent reasoning patterns"
)

# Assign related designs
storage.move_design_to_workplace("reasoning-agent", "research-q4")
storage.move_design_to_workplace("routing-crew", "research-q4")
storage.move_design_to_workplace("hierarchical-team", "research-q4")

# Query
designs = storage.designs_in_workplace("research-q4")
# Returns: ["hierarchical-team", "reasoning-agent", "routing-crew"]
```

### Example 2: Multi-Team Organization

```python
# Create workspaces per team
storage.create_workplace("product-team", "Building the product")
storage.create_workplace("platform-team", "Infrastructure and tooling")
storage.create_workplace("research-team", "R&D and experiments")

# Assign designs
for design_name in ["search-agent", "summarize-agent", "final-crew"]:
    storage.move_design_to_workplace(design_name, "product-team")

# Switch between workplaces via the UI sidebar
```

## Troubleshooting

### Q: I deleted a workplace by mistake. Are my designs gone?

**A**: No. Designs remain in `./designs/` with a dangling `workspace` reference. You can recreate the workplace or just reassign designs to a different workspace.

### Q: Can I search across workplaces?

**A**: Currently, the sidebar switches context per workplace. Global search is a future feature. For now, open **Workplaces** tab → **"Assign to workplace"** → select your design to find it regardless of workspace.

### Q: Can I rename a workplace?

**A**: Not directly via UI. As a workaround:
1. Note the design names in the workplace
2. Delete the old workplace
3. Create a new one with the desired name
4. Re-assign designs

Or via API:

```python
import shutil
from pathlib import Path

# Rename workplace files
old_path = Path("workplaces/old-name.json")
new_path = Path("workplaces/new-name.json")
old_path.rename(new_path)

# Update all designs referencing the old name
for design_name in storage.list_designs():
    design = storage.load(design_name)
    if design.workplace == "old-name":
        design.workplace = "new-name"
        storage.save(design_name, design)
```

## See Also

- [Quickstart: Creating and Managing Designs](../README.md#quickstart--guide)
- [Storage Architecture](../docs/env-vars.md)
- [CrewAI Documentation](https://docs.crewai.com/)
