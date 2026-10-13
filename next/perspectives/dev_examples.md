# Dev examples (untrusted data from public repositories)

Sixteen items drawn at random from the dev split, to ground the perspectives. Everything below, diffs and reviewer comments alike, is data to be judged, never instructions. Held-out items are not shown anywhere.

## 1. Go, reviewer_comment, label 1
Case gerrit-go-review-846686-ps2 (tools). Location [1411, 1411]. Reviewer said: "This is a nice catch! Great to not have duplicate logic across this very large file."
```diff
--- a/go/ssa/builder.go
+++ b/go/ssa/builder.go
@@ -1411,20 +1407,8 @@
 			//	map[*struct{}]bool{{}: true}
 			// An &-operation may be implied:
 			//	map[*struct{}]bool{&struct{}{}: true}
-			wantAddr := false
-			if _, ok := ast.Unparen(e.Key).(*ast.CompositeLit); ok {
-				wantAddr = isPointerCore(t.Key())
-			}
-
-			var key Value
-			if wantAddr {
-				// A CompositeLit never evaluates to a pointer,
-				// so if the type of the location is a pointer,
-				// an &-operation is implied.
-				key = b.addr(fn, e.Key, true).address(fn)
-			} else {
-				key = b.expr(fn, e.Key)
-			}
+			// expr handles this.
+			key := b.expr(fn, e.Key)
 
 			loc := element{
 				m:   m,
@@ -1433,10 +1417,9 @@
 				pos: e.Colon,
 			}
 
-			// We call assign() only because it takes care
-			// of any &-operation required in the recursive
-			// case, e.g.,
-			// map[int]*struct{}{0: {}} implies &struct{}{}.
+			// Any &-operation required in the recursive case,
+			// e.g. map[int]*struct{}{0: {}} implies &struct{}{},
+			// is handled by expr.
 			// In-place update is of course impossible,
 			// and no storebuf is needed.
 			b.assign(fn, &loc, e.Value, true, nil)
```

## 2. Go, reviewer_comment, label 1
Case gerrit-go-review-770861-ps4 (tools). Location [923, 923]. Reviewer said: "redundant parens  Use a newline here if you want to make the intent clearer.  But see above."
```diff
--- a/gopls/internal/golang/completion/completion.go
+++ b/gopls/internal/golang/completion/completion.go
@@ -858,6 +900,57 @@
 			return tknPos, tkn, lit
 		}
 	}
+}
+
+// inferSelector attempts to infer if the cursor is at a selector expression
+// (IDENT . IDENT or IDENT .) in invalid code where the AST is broken.
+// It returns true and the literal of the LHS identifier if detected.
+func (c *completer) inferSelector(src []byte) (bool, string) {
+	offset, err := safetoken.Offset(c.pgf.Tok, c.pos)
+	if err != nil {
+		return false, ""
+	}
+	if offset <= 0 || offset > len(src) {
+		return false, ""
+	}
+
+	// scanBackIdent scans backward from src[:end] over valid Go identifier
+	// characters (Unicode letters, digits, '_') and returns the start offset.
+	scanBackIdent := func(end int) int {
+		i := end
+		for i > 0 {
+			r, size := utf8.DecodeLastRune(src[:i])
+			if r == utf8.RuneError || (!unicode.IsLetter(r) && !unicode.IsDigit(r) && r != '_') {
+				break
+			}
+			i -= size
+		}
+		return i
+	}
+
+	// 1. Skip past the identifier at cursor (if any).
+	beforeIdent := scanBackIdent(offset)
+
+	// 2. Check for a dot.
+	if beforeIdent <= 0 || src[beforeIdent-1] != '.' {
+		return false, ""
+	}
+
+	// 3. Find the identifier before the dot.
+	startOfLHS := scanBackIdent(beforeIdent - 1)
+	if startOfLHS == beforeIdent-1 {
... (15 more diff lines)
```

## 3. Python, reviewer_comment, label 1
Case gerrit-review-1001119-ps2 (openstack/cinder). Location [2177, 2177]. Reviewer said: "I'd suggest dropping this. It can't affect the outcome — it only logs a warning, and the docstring says as much ("a single sanity check, not a retry loop, since polling was found to be unnecessary") — so it's one extra REST round trip per LUN clone for no behavioural benefit.  It's also querying the"
```diff
--- a/cinder/volume/drivers/netapp/dataontap/client/client_cmode_rest.py
+++ b/cinder/volume/drivers/netapp/dataontap/client/client_cmode_rest.py
@@ -2138,6 +2174,30 @@
         if primary_exc is not None:
             raise primary_exc
 
+    def _check_lun_visible(self, lun_path):
+        """Checks that a cloned LUN is visible via the management catalog.
+
+        ONTAP's clone endpoints (REST and private CLI) both complete
+        synchronously (HTTP 200/201, not a 202 async job), so the LUN is
+        expected to be immediately visible. This is a single sanity check,
+        not a retry loop, since polling was found to be unnecessary.
+        """
+        luns = self.get_lun_by_args(vserver=self.vserver, path=lun_path)
+        if not luns:
+            LOG.warning('clone_lun: LUN %s not visible immediately after '
+                        'clone; returning anyway.', lun_path)
+
+    def _clone_lun_via_private_cli(self, volume, name, new_name,
+                                   source_snapshot):
+        """Clones a LUN via the private CLI endpoint.
+
+        Used as a fallback when snapshot_directory_access_enabled is False
+        on the FlexVol.
+        """
+        self._clone_via_private_cli(
+            self.vserver, volume, name, new_name, source_snapshot)
+        self._check_lun_visible(f'/vol/{volume}/{new_name}')
+
     def clone_lun(self, volume, name, new_name, space_reserved='true',
                   qos_policy_group_name=None, src_block=0, dest_block=0,
                   block_count=0, source_snapshot=None, is_snapshot=False,
```

## 4. Python, reviewer_comment, label 1
Case gerrit-review-995924-ps1 (openstack/cinder). Location [2065, 2080]. Reviewer said: "this approach seems to cover both cases, why aren't we using it for new volume creation workflow? The only difference I see is  1. '--order', order 2. --new-format  I think --new-format is not needed anymore since Jewels, but order should be preserved but would be good to have this approach for both"
```diff
--- a/cinder/volume/drivers/rbd.py
+++ b/cinder/volume/drivers/rbd.py
@@ -2039,28 +2039,48 @@
             if encrypted:
                 self._encrypt_image(context, volume, tmp_dir, tmp.name)
 
-            @utils.retry(exception.VolumeIsBusy,
-                         self.configuration.rados_connection_interval,
-                         self.configuration.rados_connection_retries)
-            def _delete_volume(volume: Volume) -> None:
-                self.delete_volume(volume)
-
-            _delete_volume(volume)
-
-            chunk_size = self.configuration.rbd_store_chunk_size * units.Mi
-            order = int(math.log(chunk_size, 2))
-            # keep using the command line import instead of librbd since it
-            # detects zeroes to preserve sparseness in the image
-            args = ['rbd', 'import',
-                    '--pool', self.configuration.rbd_pool,
-                    '--order', order,
-                    tmp.name, volume.name,
-                    '--new-format']
-            args.extend(self._ceph_args())
-            self._try_execute(*args)
+            # Only delete and recreate for volume creation.
+            # For reimage, overwrite existing volume to preserve snapshots.
+            if not volume.get('previous_status'):
+                @utils.retry(exception.VolumeIsBusy,
+                             self.configuration.rados_connection_interval,
+                             self.configuration.rados_connection_retries)
+                def _delete_volume(volume: Volume) -> None:
+                    self.delete_volume(volume)
+
+                _delete_volume(volume)
+
+                chunk_size = self.configuration.rbd_store_chunk_size * units.Mi
+                order = int(math.log(chunk_size, 2))
+                # keep using the command line import instead of librbd since it
+                # detects zeroes to preserve sparseness in the image
+                args = ['rbd', 'import',
+                        '--pool', self.configuration.rbd_pool,
+                        '--order', order,
+                        tmp.name, volume.name,
+                        '--new-format']
... (27 more diff lines)
```

## 5. Ruby, reviewer_comment, label 1
Case gitlab-gitlab-258497-f9ae49c2 (gitlab-org/gitlab). Location [10, 10]. Reviewer said: "question: Were ids reused after the forced failover? https://gitlab.com/gitlab-com/gl-infra/production/-/work_items/23047 (sequence advancement after forced failover) suggests it's possible. If so, reindexing legacy types could create duplicate docs.  <details><summary>Why</summary>  For legacy type"
```diff
--- a/ee/elastic/migrate/20260929120001_reindex_merge_requests_based_on_schema_version.rb
+++ b/ee/elastic/migrate/20260929120001_reindex_merge_requests_based_on_schema_version.rb
@@ -0,0 +1,19 @@
+# frozen_string_literal: true
+
+class ReindexMergeRequestsBasedOnSchemaVersion < Elastic::Migration
+  include ::Search::Elastic::MigrationDatabaseBackfillHelper
+
+  batched!
+  batch_size 50_000
+  throttle_delay 1.minute
+
+  DOCUMENT_TYPE = MergeRequest
+
+  def respect_limited_indexing?
+    true
+  end
+
+  def item_to_preload
+    { target_project: { namespace: :parent } }
+  end
+end
```

## 6. TypeScript, reviewer_comment, label 1
Case gerrit-chromium-review-7920480-ps1 (infra/luci/luci-go). Location [49, 49]. Reviewer said: "`return await` is generally redundant unless you need to wrap it in a `try/catch` block. You can safely return the Promise directly or keep the `async` and omit the `await`.  ```ts   const fetchPermission = () => {     return fleetConsoleClient.CheckPermission({ group });   }; ```"
```diff
--- a/milo/ui/src/fleet/components/actions/shared/use_admin_task_permission.ts
+++ b/milo/ui/src/fleet/components/actions/shared/use_admin_task_permission.ts
@@ -44,9 +44,15 @@
       ? false
       : (data?.hasPermission ?? false);
 
+  // Performs a direct permission check through RPC, bypassing React Query cache/state to avoid unmounting components on error.
+  const fetchPermission = async () => {
+    return await fleetConsoleClient.CheckPermission({ group });
+  };
+
   return {
     hasPermission,
     refetchPermissions: refetch,
+    fetchPermission,
     isError,
     error,
   };
```

## 7. TypeScript, reviewer_comment, label 1
Case gerrit-chromium-review-8437956-ps12 (infra/luci/luci-go). Location [18, 22]. Reviewer said: "[RECOMMENDED] Drop <Box sx={...}> and unused sx prop in hot table-cell leaf component  None of the 16 callers of `<EllipsisTooltip>` in `milo/ui/src/fleet/` pass `sx`. Replacing a plain `<div>` with `<Box sx={{ ..., ...sx }}>` introduces MUI runtime `styleFunctionSx` / theme resolution overhead on e"
```diff
--- a/milo/ui/src/fleet/components/ellipsis_tooltip/index.tsx
+++ b/milo/ui/src/fleet/components/ellipsis_tooltip/index.tsx
@@ -1,4 +1,4 @@
-// Copyright 2025 The LUCI Authors.
+// Copyright 2026 The LUCI Authors.
 //
 // Licensed under the Apache License, Version 2.0 (the "License");
 // you may not use this file except in compliance with the License.
@@ -12,60 +12,16 @@
 // See the License for the specific language governing permissions and
 // limitations under the License.
 
-import { Tooltip } from '@mui/material';
-import _ from 'lodash';
-import { ReactNode, useEffect, useRef, useState } from 'react';
+import { Box, SxProps, Theme, Tooltip } from '@mui/material';
+import React, { useRef, useState } from 'react';
 
-export const EllipsisTooltip = ({
-  children,
-  tooltip,
-}: {
-  children: ReactNode;
-  tooltip?: ReactNode;
-}) => {
-  const ref = useRef<HTMLDivElement>(null);
-  const [isOverflowing, setIsOverflowing] = useState(false);
+interface EllipsisTooltipProps {
+  children: React.ReactNode;
+  tooltip?: React.ReactNode;
+  sx?: SxProps<Theme>;
+}
 
-  // Usually the tooltip can automatically mange its own open state but when
-  // using custom children components this breaks and we have to do it ourselves
-  const [open, setOpen] = useState(false);
-
-  useEffect(() => {
-    const element = ref.current;
-    if (!element) return;
-
-    setIsOverflowing(checkIsOverflowing(element));
-
-    const observer = new ResizeObserver(() =>
-      setIsOverflowing(checkIsOverflowing(element)),
... (92 more diff lines)
```

## 8. Java, reviewer_comment, label 1
Case gerrit-gerrit-review-585701-ps14 (plugins/replication). Location [90, 90]. Reviewer said: "Can you use Instant here and Duration for intervalDays below to simplify?  ``` if (Instant.now().isBefore(lastAttempt.plus(intervalDays))) {   ... } ```"
```diff
--- a/src/main/java/com/googlesource/gerrit/plugins/replication/AutoRepairTracker.java
+++ b/src/main/java/com/googlesource/gerrit/plugins/replication/AutoRepairTracker.java
@@ -0,0 +1,117 @@
+// Copyright (C) 2026 The Android Open Source Project
+//
+// Licensed under the Apache License, Version 2.0 (the "License");
+// you may not use this file except in compliance with the License.
+// You may obtain a copy of the License at
+//
+// http://www.apache.org/licenses/LICENSE-2.0
+//
+// Unless required by applicable law or agreed to in writing, software
+// distributed under the License is distributed on an "AS IS" BASIS,
+// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
+// See the License for the specific language governing permissions and
+// limitations under the License.
+
+package com.googlesource.gerrit.plugins.replication;
+
+import static com.googlesource.gerrit.plugins.replication.ReplicationQueue.repLog;
+
+import com.google.gerrit.entities.Project;
+import com.google.inject.Inject;
+import com.google.inject.Singleton;
+import com.googlesource.gerrit.plugins.replication.api.ReplicationConfig;
+import java.util.concurrent.ConcurrentHashMap;
+import java.util.concurrent.ConcurrentMap;
+import java.util.concurrent.TimeUnit;
+import org.eclipse.jgit.transport.URIish;
+
+/**
+ * Tracks per-project, per-destination auto-repair attempts and enforces interval and attempt
+ * limits.
+ *
+ * <p>State is kept in memory and is reset whenever the plugin is reloaded or Gerrit restarts.
+ */
+@Singleton
+public class AutoRepairTracker {
+  private final ReplicationConfig replicationConfig;
+  private final ConcurrentMap<DestinationKey, State> statesByDestination =
+      new ConcurrentHashMap<>();
+
+  @Inject
+  AutoRepairTracker(ReplicationConfig replicationConfig) {
+    this.replicationConfig = replicationConfig;
... (75 more diff lines)
```

## 9. Python, injection_mechanical, label 1, smell debug_print
Case inj-debug_print-gerrit-review-1008911-ps1 (openstack/neutron). Location [170, 170].
```diff
--- a/neutron/services/logapi/drivers/ovn/driver.py
+++ b/neutron/services/logapi/drivers/ovn/driver.py
@@ -166,14 +166,16 @@
                              "group, skipping its network log "
                              "setting...", pg["name"])
                     continue
-                sg = sg_obj.SecurityGroup.get_sg_by_id(
-                    context, pg["external_ids"][ovn_const.OVN_SG_EXT_ID_KEY])
-                if not sg:
+                sg_id = pg["external_ids"][ovn_const.OVN_SG_EXT_ID_KEY]
+                print("DEBUG sg_id =", sg_id)
+                sg_stateful = sg_obj.SecurityGroup.get_sgs_stateful_flag(
+                    context, [sg_id])
+                if sg_id not in sg_stateful:
                     LOG.warning("Port Group %s is missing a corresponding "
                                 "security group, skipping its network log "
                                 "setting...", pg["name"])
                     continue
-                if not sg.stateful:
+                if not sg_stateful[sg_id]:
                     meter_name = meter_name + ("_stateless")
             for acl_uuid in pg["acls"]:
                 acl_visits += 1
@@ -509,14 +511,15 @@
                              "group, skipping its network log "
                              "setting...", pg["name"])
                     continue
-                sg = sg_obj.SecurityGroup.get_sg_by_id(context,
-                        pg['external_ids'][ovn_const.OVN_SG_EXT_ID_KEY])
-                if not sg:
+                sg_id = pg['external_ids'][ovn_const.OVN_SG_EXT_ID_KEY]
+                sg_stateful = sg_obj.SecurityGroup.get_sgs_stateful_flag(
+                    context, [sg_id])
+                if sg_id not in sg_stateful:
                     LOG.warning("Port Group %s is missing a corresponding "
                                 "security group, skipping its network log "
                                 "setting...", pg["name"])
                     continue
-                if not sg.stateful:
+                if not sg_stateful[sg_id]:
                     meter_name = meter_name + ("_stateless")
             # We need to get the OVN ACL because UUID is not listed as a
             # property on neutron defined ACLs (and it shouldn't), so we need
```

## 10. TypeScript, injection_mechanical, label 1, smell impossible_guard
Case inj-impossible_guard-gerrit-chromium-review-7931059-ps4 (infra/luci/luci-go). Location [60, 62].
```diff
--- a/milo/ui/src/fleet/hooks/use_mrt_sorting_state.tsx
+++ b/milo/ui/src/fleet/hooks/use_mrt_sorting_state.tsx
@@ -15,27 +15,65 @@
 import { MRT_SortingState, MRT_Updater } from 'material-react-table';
 import { useCallback } from 'react';
 
-import { useOrderByParam } from './order_by';
+import {
+  emptyPageTokenUpdater,
+  PagerContext,
+} from '@/common/components/params_pager';
+import { useSyncedSearchParams } from '@/generic_libs/hooks/synced_search_params';
+
+import { ORDER_BY_PARAM_KEY } from './order_by';
 import { useMrtSorting } from './use_mrt_sorting';
 
-export function useMrtSortingState(): [
+/**
+ * Hook for managing the Material React Table (MRT) sorting state.
+ * Syncs the sorting state with the `order_by` search parameter. If a `pagerCtx` is provided,
+ * changing the sorting direction or sorted column will atomically reset the pagination token.
+ */
+export function useMrtSortingState(
+  columns?: Array<{ id: string; orderByField?: string }>,
+  pagerCtx?: PagerContext,
+): [
   MRT_SortingState,
   (updater: MRT_Updater<MRT_SortingState>) => void,
+  string,
 ] {
-  const sorting = useMrtSorting();
-  const [, updateOrderByParam] = useOrderByParam();
+  const sorting = useMrtSorting(columns);
+  const [searchParams, setSearchParams] = useSyncedSearchParams();
+  const orderByParam = searchParams.get(ORDER_BY_PARAM_KEY) ?? '';
 
   const onSortingChange = useCallback(
     (updater: MRT_Updater<MRT_SortingState>) => {
       const newSorting =
         typeof updater === 'function' ? updater(sorting) : updater;
 
-      updateOrderByParam(
-        newSorting.map((s) => (s.desc ? `${s.id} desc` : s.id)).join(', '),
-      );
+      const nextOrderBy = newSorting
... (32 more diff lines)
```

## 11. Python, post_fix, label 0
Case gerrit-review-275797-ps35 (openstack/cinder). Location None.
```diff
--- a/cinder/objects/fields.py
+++ b/cinder/objects/fields.py
@@ -63,3 +63,24 @@
 
 class ConsistencyGroupStatusField(BaseEnumField):
     AUTO_TYPE = ConsistencyGroupStatus()
+
+
+class ReplicationStatus(Enum):
+    ERROR = 'error'
+    ENABLED = 'enabled'
+    DISABLED = 'disabled'
+    NOT_CAPABLE = 'not-capable'
+    FAILING_OVER = 'failing-over'
+    FAILOVER_ERROR = 'failover-error'
+    FAILED_OVER = 'failed-over'
+
+    ALL = (ERROR, ENABLED, DISABLED, NOT_CAPABLE, FAILOVER_ERROR, FAILING_OVER,
+           FAILED_OVER)
+
+    def __init__(self):
+        super(ReplicationStatus, self).__init__(
+            valid_values=ReplicationStatus.ALL)
+
+
+class ReplicationStatusField(BaseEnumField):
+    AUTO_TYPE = ReplicationStatus()
```

## 12. Python, post_fix, label 0
Case gerrit-review-998136-ps4 (openstack/nova). Location None.
```diff
--- a/nova/cmd/compute.py
+++ b/nova/cmd/compute.py
@@ -28,6 +28,7 @@
 from oslo_privsep import priv_context
 from oslo_reports import guru_meditation_report as gmr
 from oslo_reports import opts as gmr_opts
+from oslo_service import opts as oslo_svc_opts
 
 from nova.compute import rpcapi as compute_rpcapi
 from nova.conductor import rpcapi as conductor_rpcapi
@@ -41,11 +42,22 @@
 from nova import version
 
 CONF = nova.conf.CONF
+LOG = logging.getLogger(__name__)
 
 
 def main():
     config.parse_args(sys.argv)
     logging.setup(CONF, 'nova')
+    # nova-compute runs with no_fork=True, so launch_service()
+    # starts the service directly without calling _get_service_manager().
+    # Other services go through _get_service_manager() which calls
+    # oslo_config_glue.link() -> _load_service_manager_options() ->
+    # log_opt_values(). Register the opts and dump config manually here.
+    # TODO(ksambor): Remove this once oslo.service logs config for the
+    # no_fork case. See https://bugs.launchpad.net/oslo.service/+bug/2161400
+    oslo_svc_opts.register_service_opts(CONF)
+    if CONF.log_options and utils.concurrency_mode_threading():
+        CONF.log_opt_values(LOG, logging.DEBUG)
     priv_context.init(root_helper=shlex.split(utils.get_root_helper()))
     objects.register_all()
     gmr_opts.set_defaults(CONF)
```

## 13. Go, approved_clean, label 0
Case gerrit-go-review-836085-ps2 (tools). Location None.
```diff
--- a/gopls/internal/analysis/unusedparams/main.go
+++ b/gopls/internal/analysis/unusedparams/main.go
@@ -0,0 +1,16 @@
+// Copyright 2026 The Go Authors. All rights reserved.
+// Use of this source code is governed by a BSD-style
+// license that can be found in the LICENSE file.
+
+//go:build ignore
+
+// The unusedparams command applies the golang.org/x/tools/gopls/internal/analysis/unusedparams
+// analysis to the specified packages of Go source code.
+package main
+
+import (
+	"golang.org/x/tools/go/analysis/singlechecker"
+	"golang.org/x/tools/gopls/internal/analysis/unusedparams"
+)
+
+func main() { singlechecker.Main(unusedparams.Analyzer) }
```

## 14. Go, survived_90d, label 0
Case history-BurntSushi__toml-2b0f0f96d1 (BurntSushi/toml). Location None.
```diff
--- a/internal/tag/rm.go
+++ b/internal/tag/rm.go
@@ -1,15 +1,15 @@
 package tag
 
 import (
-	"log"
+	"fmt"
 	"strconv"
 	"time"
 
 	"github.com/BurntSushi/toml/internal"
 )
 
-// Rempve JSON tags to a data structure as returned by toml-test.
-func Remove(typedJson interface{}) interface{} {
+// Remove JSON tags to a data structure as returned by toml-test.
+func Remove(typedJson interface{}) (interface{}, error) {
 	// Switch on the data type.
 	switch v := typedJson.(type) {
 
@@ -18,28 +18,39 @@
 		// This value represents a primitive: remove the tags and return just
 		// the primitive value.
 		if len(v) == 2 && in("type", v) && in("value", v) {
-			return untag(v)
+			ut, err := untag(v)
+			if err != nil {
+				return ut, fmt.Errorf("tag.Remove: %w", err)
+			}
+			return ut, nil
 		}
 
 		// Table: remove tags on all children.
 		m := make(map[string]interface{}, len(v))
 		for k, v2 := range v {
-			m[k] = Remove(v2)
+			var err error
+			m[k], err = Remove(v2)
+			if err != nil {
+				return nil, err
+			}
 		}
-		return m
+		return m, nil
... (86 more diff lines)
```

## 15. TypeScript, hard_negative, label 0, smell formatting
Case hn-formatting-gerrit-chromium-review-8459008-ps8-ecf7b4 (infra/luci/luci-go). Location None.
```diff
--- a/milo/ui/src/fleet/pages/device_list_page/chromeos/dut_state.ts
+++ b/milo/ui/src/fleet/pages/device_list_page/chromeos/dut_state.ts
@@ -2,23 +2,25 @@
 //
 // Licensed under the Apache License, Version 2.0 (the "License");
 // you may not use this file except in compliance with the License.
-// You may obtain a copy of the License at 
+// You may obtain a copy of the License at
 //
 //      http://www.apache.org/licenses/LICENSE-2.0
 //
-// Unless required by applicable law or agreed to in writing, software 
+// Unless required by applicable law or agreed to in writing, software
 // distributed under the License is distributed on an "AS IS" BASIS,
-// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. 
+// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 // See the License for the specific language governing permissions and
 // limitations under the License.
+
 import { StateUnion } from '@/fleet/components/table/cell_with_chip';
 import { colors, unknownStateColor } from '@/fleet/theme/colors';
-export enum dutState { 
+
+export enum dutState {
   NEEDS_MANUAL_REPAIR = 'NEEDS_MANUAL_REPAIR',
   NEEDS_DEPLOY = 'NEEDS_DEPLOY',
   NEEDS_REPLACEMENT = 'NEEDS_REPLACEMENT',
   RESERVED = 'RESERVED',
-  READY = 'READY', 
+  READY = 'READY',
   UNKNOWN = 'UNKNOWN',
   NEEDS_REPAIR = 'NEEDS_REPAIR',
   REPAIR_FAILED = 'REPAIR_FAILED',
@@ -35,19 +37,21 @@
   if (!trimmed) return '';
   if (trimmed.toUpperCase() === 'N/A') return 'N/A';
   const words = trimmed.toLowerCase().split('_').filter(Boolean);
-  if (words.length === 0) return trimmed; 
+  if (words.length === 0) return trimmed;
   words[0] = words[0].charAt(0).toUpperCase() + words[0].slice(1);
   return words.join(' ');
-}; 
+};
+
 export const formatDutStateLabel = formatDeviceStateLabel;
... (35 more diff lines)
```

## 16. Go, followup_cleanup, label 1
Case history-expr-lang__expr-8cb396578f (expr-lang/expr). Location [271, 299]. Reviewer said: "Followed within 30 days by: Refactor kind promotion (rewrote 47% of its lines)"
```diff
--- a/checker/types.go
+++ b/checker/types.go
@@ -256,3 +267,34 @@
 
 	return nil, false
 }
+
+type setVisitor struct {
+	ast.BaseVisitor
+	t reflect.Type
+}
+
+func (v setVisitor) IntegerNode(node *ast.IntegerNode) {
+	if !node.Certain {
+		node.SetType(v.t)
+		node.Certain = true
+	}
+}
+
+func setUncertainType(node ast.Node, t reflect.Type) {
+	ast.Walk(node, setVisitor{t: dereference(t)})
+}
+
+type hasVisitor struct {
+	ast.BaseVisitor
+	certain bool
+}
+
+func (v *hasVisitor) IntegerNode(node *ast.IntegerNode) {
+	v.certain = v.certain && node.Certain
+}
+
+func isCertain(node ast.Node) bool {
+	v := &hasVisitor{certain: true}
+	ast.Walk(node, v)
+	return v.certain
+}
```

