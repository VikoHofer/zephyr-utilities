# Creation and deletion lifecycle audit

This audit covers the implemented application, including persistent records, browser
preferences and transient observations. Planned features do not create user data yet.

| Created item | Removal or reconciliation | Remaining limitation |
| --- | --- | --- |
| Application source directory | Move/delete with the file manager; Creator checks saved directory identity on open, focus and Refresh list | Reviewed import/reconnect is available; source-folder deletion still uses the file manager |
| Saved application registration | Remove saved application, including dependent configurations and attempt records; unavailable folders have a separate cleanup section | Removal has no in-app undo; source/build files stay on disk |
| Named build configuration | Remove configuration; captured attempts retain their profile | Named choices can be renamed/edited; Original keeps its reserved name but its profile can be updated |
| Configure/build attempt history | Remove history entry after the operation finishes | Managed output records survive history removal; Clean output explicitly reviews and removes their directories |
| Managed build output | Reviewed cleanup removes the owned tree and records a cleaned state; verified reuse records a new attempt and labels earlier shared output as superseded | Legacy outputs need ownership checks; cleanup errors may leave partial output requiring another review |
| Creation/build preview | Consumed once, invalidated by relevant removal, expires after 30 minutes; bounded to 32 server plans | No persistent data to delete |
| Memory snapshot / imported report / annotations | Existing Delete action removes the snapshot, raw reports and annotations; selection/comparison state reconciles | No in-app undo |
| Content-addressed captured objects | Shared objects retained when a snapshot is deleted | Unreferenced object garbage collection is not implemented; surviving snapshots and active captures may share objects |
| Recent Memory search paths | Clear recent paths clears browser history and suggestions | Does not remove the currently entered path or any files |
| Environment observation | Latest observation replaces the workspace pointer; old immutable observations remain stored | No observation-history browser or explicit pruning; saved profiles reference observation identity |
| Memory build discovery | Each search rescans current folders | Existing search results refresh on another search |
| Inspector build/source observations | Bounded in-memory caches with expiry; live reads validate paths/artifacts | Retained evidence is an observation, not a promise that files still exist |
| Shared jobs | Active jobs can be cancelled; completed history is bounded and evicted on subsequent submissions | Durable feature results have their own removal rules |
| Workspace selection | One selected path, replaced by the workspace picker | No accumulating workspace registry |
| Device Tree Editor / TinyML experiments | Planned; no implemented persistent entities | Lifecycle must accompany future creation features |

No automatic scan removes saved evidence or firmware inputs. Metadata removal and disk
cleanup have different scopes and their controls must describe that distinction.
See [Project Creator](project-creator.md) and the [Memory contract](implementation-contract.md).
