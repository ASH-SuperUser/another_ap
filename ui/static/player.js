/* another_ap player: queue + drag-reorder + local/server shuffle +
   song-list loops + sleep + speed/volume. playlist.html (full) and
   audio.html (single) share it. No deps. */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);

  function fmt(s) {
    if (s == null || isNaN(s)) return "--:--";
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return h ? h + ":" + String(m).padStart(2, "0") + ":" + String(x).padStart(2, "0")
             : m + ":" + String(x).padStart(2, "0");
  }

  function shuffled(arr) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  function initPlayer(opts) {
    // opts: {audio, tracks: [{id,title,artist,duration_sec,stream_url}], startIndex,
    //        playlistId?, removable?, reorderable?, onRemove?, onTrack?,
    //        onPersistOrder?(ids)->Promise, onDirty?(dirty)}
    const audio = opts.audio;
    const listEl = $("#queue");
    const canReorder = !!opts.reorderable && (opts.tracks || []).length > 1;
    let order = (opts.tracks || []).map((_, i) => i); // live play order (indices)
    let savedOrder = order.slice();                   // last server-persisted order
    let pos = Math.max(0, opts.startIndex || 0);
    let shuffleMode = "off"; // off | local | server
    let trackLoop = "off";   // off | inf | n  (repeat current song: forever or N total plays)
    let songN = 2, songLeft = 0;
    let listLoop = "off";    // off | inf | n  (repeat whole playlist)
    let listN = 2, listLeft = 0, passNum = 1;
    let sleepTimer = null, sleepAt = 0;
    let dirty = false, saving = false;
    let dragFrom = -1;
    let selectMode = false;
    const selected = new Set(); // track indices (into opts.tracks) ticked in bulk-edit
    window.apPlayerActive = true;

    const els = {
      play: $("#btn-play"), prev: $("#btn-prev"), next: $("#btn-next"),
      seek: $("#seek"), tCur: $("#t-cur"), tDur: $("#t-dur"),
      vol: $("#vol"), volVal: $("#vol-val"), mute: $("#btn-mute"),
      speed: $("#speed"), speedC: $("#speed-custom"), speedSet: $("#speed-set"),
      shuffle: $("#btn-shuffle"), shuffleSrv: $("#btn-shuffle-srv"),
      loop: $("#btn-loop"), loopNSong: $("#loop-n-song"),
      loopList: $("#btn-looplist"), loopN: $("#loop-n"),
      save: $("#btn-save-order"),
      sleep: $("#sleep"), sleepC: $("#sleep-custom"), sleepSet: $("#sleep-set"),
      sleepLeft: $("#sleep-left"),
      nowT: $("#now-title"), nowA: $("#now-artist"), nowCount: $("#now-count"),
      nowD: $("#now-desc"), nowThumb: $("#now-thumb"), status: $("#p-status"),
    };

    function cur() { return opts.tracks[order[pos]]; }
    function orderIds() { return order.map((i) => opts.tracks[i].id); }
    function idxOfId(id) { return opts.tracks.findIndex((t) => t.id === id); }
    function status(msg) { if (els.status) els.status.textContent = "$ " + msg; }

    /* media session: browser media keys + lock screen / notification UI */
    function setMediaSession(t) {
      if (!navigator || !navigator.mediaSession) return;
      try {
        navigator.mediaSession.metadata = new MediaMetadata({
          title: t.title || "",
          artist: t.artist || "",
          album: t.description || "",
          artwork: t.thumb_url ? [{ src: t.thumb_url, sizes: "480x480", type: "image/png" }] : [],
        });
      } catch (_) {}
    }
    function setupMediaSession() {
      if (!navigator || !navigator.mediaSession) return;
      try {
        navigator.mediaSession.setActionHandler("play", () => { audio.play().catch(() => {}); });
        navigator.mediaSession.setActionHandler("pause", () => { audio.pause(); });
        navigator.mediaSession.setActionHandler("previoustrack", prev);
        navigator.mediaSession.setActionHandler("nexttrack", () => { next(true); });
      } catch (_) {}
    }
    function setPositionState() {
      if (!navigator || !navigator.mediaSession) return;
      try {
        const d = audio.duration || (cur() && cur().duration_sec) || 0;
        navigator.mediaSession.setPositionState({
          duration: d,
          playbackRate: audio.playbackRate,
          position: audio.currentTime,
        });
      } catch (_) {}
    }

    function markDirty() {
      dirty = order.join(",") !== savedOrder.join(",");
      if (els.save) els.save.hidden = !dirty;
      if (opts.onDirty) { try { opts.onDirty(dirty); } catch (_) {} }
    }

    async function persistOrder(ids) {
      if (!opts.onPersistOrder) return false;
      try { await opts.onPersistOrder(ids); return true; }
      catch (e) { status("server save failed: " + (e && e.message || e)); return false; }
    }

    async function doSave() {
      if (!dirty || saving) return;
      if (!opts.playlistId) { status("nothing to save (not a playlist)"); return; }
      saving = true;
      const ok = await persistOrder(orderIds());
      saving = false;
      if (ok) { savedOrder = order.slice(); markDirty(); status("order saved to server [w]"); }
      else status("save FAILED — order still local only");
    }

    function moveItem(fromP, toP, after) {
      if (!canReorder) return;
      if (fromP === toP && !after) return; // dropped back on itself: no-op
      let ins = after ? toP + 1 : toP;
      const curIdx = order[pos];
      const [m] = order.splice(fromP, 1);
      if (fromP < ins) ins -= 1;
      if (ins === fromP) { order.splice(fromP, 0, m); return; }
      order.splice(ins, 0, m);
      pos = order.indexOf(curIdx);
      renderQueue();
      markDirty();
      status("moved to #" + (ins + 1) + (dirty ? " (unsaved — [w] saves to server)" : ""));
    }

    // ---- bulk selection (checkbox edit mode; page owns the buttons) ----
    function getSelectedIds() {
      const out = [];
      order.forEach((ti) => { if (selected.has(ti)) out.push(opts.tracks[ti].id); });
      return out;
    }
    function setSelectMode(on) {
      selectMode = !!on;
      if (!selectMode) selected.clear();
      renderQueue();
      if (opts.onSelection) { try { opts.onSelection(getSelectedIds()); } catch (_) {} }
    }
    function selectAll(none) {
      selected.clear();
      if (!none) order.forEach((ti) => selected.add(ti));
      renderQueue();
      if (opts.onSelection) { try { opts.onSelection(getSelectedIds()); } catch (_) {} }
    }

    function clearDropMarks() {
      Array.from(listEl.children).forEach((c) => c.classList.remove("drop-before", "drop-after", "dragging"));
    }

    function renderQueue() {
      if (!listEl) return;
      listEl.innerHTML = "";
      order.forEach((ti, p) => {
        const t = opts.tracks[ti];
        const div = document.createElement("div");
        div.className = "q-item" + (p === pos ? " playing" : "");
        div.tabIndex = 0;
        div.setAttribute("role", "button");
        div.dataset.pos = p;
        if (canReorder) div.draggable = true;
        div.innerHTML = "<span class='n'>" + String(p + 1).padStart(2, "0") + "</span>" +
          (t.thumb_url ? "<img class='q-thumb' loading='lazy' alt=''>" : "") +
          "<span class='tt'></span><span class='d'>" + fmt(t.duration_sec) + "</span>";
        const qi = div.querySelector(".q-thumb");
        if (qi) qi.src = t.thumb_url;
        div.querySelector(".tt").textContent = (p === pos ? "> " : "") + t.title + " — " + t.artist;
        if (selectMode) {
          const cb = document.createElement("input");
          cb.type = "checkbox"; cb.className = "q-sel"; cb.checked = selected.has(ti);
          cb.title = "select for bulk edit";
          cb.setAttribute("aria-label", "select " + t.title);
          cb.addEventListener("click", (e) => e.stopPropagation());
          cb.addEventListener("change", (e) => {
            e.stopPropagation();
            if (cb.checked) selected.add(ti); else selected.delete(ti);
            if (opts.onSelection) { try { opts.onSelection(getSelectedIds()); } catch (_) {} }
          });
          div.insertBefore(cb, div.firstChild);
        }
        const go = () => { pos = p; songLeft = songN; syncLoopUI(); load(true); };
        div.addEventListener("click", (e) => { if (!e.target.closest("[data-rm]")) go(); });
        div.addEventListener("keydown", (e) => {
          if (e.key === "Enter") { go(); e.stopPropagation(); }
          else if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
            e.preventDefault(); e.stopPropagation();
            const to = e.key === "ArrowUp" ? p - 1 : p + 1;
            if (to >= 0 && to < order.length) moveItem(p, e.key === "ArrowUp" ? to : to, e.key === "ArrowDown");
          }
        });
        if (canReorder) {
          div.addEventListener("dragstart", (e) => {
            dragFrom = p;
            div.classList.add("dragging");
            try { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(p)); } catch (_) {}
          });
          div.addEventListener("dragend", () => { dragFrom = -1; clearDropMarks(); });
          div.addEventListener("dragover", (e) => {
            if (dragFrom < 0) return;
            e.preventDefault();
            try { e.dataTransfer.dropEffect = "move"; } catch (_) {}
            const r = div.getBoundingClientRect();
            const after = (e.clientY - r.top) > r.height / 2;
            clearDropMarks();
            div.classList.add(after ? "drop-after" : "drop-before");
            div._dropAfter = after;
          });
          div.addEventListener("dragleave", () => div.classList.remove("drop-before", "drop-after"));
          div.addEventListener("drop", (e) => {
            e.preventDefault();
            const from = dragFrom, after = !!div._dropAfter;
            dragFrom = -1; clearDropMarks();
            if (from >= 0 && !(from === p && !after)) moveItem(from, p, after);
            else if (from === p) renderQueue();
          });
        }
        if (opts.removable) {
          const rm = document.createElement("button");
          rm.className = "btn rm"; rm.textContent = "rm"; rm.setAttribute("data-rm", "1");
          rm.title = "remove from playlist";
          rm.onclick = () => opts.onRemove && opts.onRemove(t, ti);
          div.appendChild(rm);
        }
        listEl.appendChild(div);
      });
    }

    function load(autoplay) {
      const t = cur();
      if (!t) { status("queue empty"); return; }
      audio.src = t.stream_url;
      audio.playbackRate = currentSpeed();
      if (els.nowT) els.nowT.textContent = t.title;
      if (els.nowA) els.nowA.textContent = t.artist;
      if (els.nowCount) els.nowCount.textContent = (pos + 1) + "/" + order.length;
      if (els.nowThumb) {
        if (t.thumb_url) { els.nowThumb.src = t.thumb_url; els.nowThumb.hidden = false; }
        else els.nowThumb.hidden = true;
      }
      if (els.nowD) els.nowD.textContent = t.description || "";
      if (els.tDur) els.tDur.textContent = fmt(t.duration_sec);
      document.title = t.title + " // another_ap";
      setMediaSession(t);
      renderQueue();
      const qi = listEl && listEl.children[pos];
      if (qi) qi.scrollIntoView({ block: "nearest" });
      status("loaded: " + t.title + (passNum > 1 ? "  (pass #" + passNum + ")" : ""));
      if (autoplay) {
        audio.play().catch(() => { setPlayBtn(false); });
        // count a playlist play per track start (server no-ops in low disk mode)
        if (opts.playlistId && window.apApi) {
          try {
            const r = window.apApi("/api/playlists/" + opts.playlistId + "/play", { method: "POST" });
            if (r && r.catch) r.catch(() => {});
          } catch (_) {}
        }
      }
      if (opts.onTrack) opts.onTrack(t, pos);
    }

    function setPlayBtn(playing) {
      if (els.play) els.play.textContent = playing ? "pause ⏸ [space]" : "play ▶ [space]";
      if (els.play) els.play.classList.toggle("on", !!playing);
    }

    function toggle() { if (audio.paused) audio.play().catch(() => {}); else audio.pause(); }

    async function restartPass() {
      // whole-playlist loop wrapped around: fresh shuffle per pass when armed
      passNum++;
      songLeft = songN; syncLoopUI();
      if (shuffleMode === "local") {
        order = shuffled(order);
        pos = 0;
        load(true);
        markDirty();
        status("pass #" + passNum + ": fresh local mix" + (dirty ? " (unsaved — [w] keeps it)" : ""));
      } else if (shuffleMode === "server" && opts.playlistId) {
        const ids = shuffled(savedOrder.map((i) => opts.tracks[i].id));
        order = ids.map(idxOfId).filter((i) => i >= 0);
        if (!order.length) order = savedOrder.slice();
        pos = 0;
        renderQueue();
        const ok = await persistOrder(ids);
        if (ok) savedOrder = order.slice();
        markDirty();
        load(true);
        status(ok ? "pass #" + passNum + ": fresh mix saved to server"
                  : "pass #" + passNum + ": server save FAILED — order local only");
      } else {
        pos = 0;
        load(true);
      }
    }

    function next(auto) {
      if (auto) {
        // track ended by itself: song loop gets first refusal (N = total plays)
        if (trackLoop === "inf") { audio.currentTime = 0; audio.play().catch(() => {}); return; }
        if (trackLoop === "n" && songLeft > 1) {
          songLeft--; syncLoopUI();
          audio.currentTime = 0; audio.play().catch(() => {});
          status("song x" + songLeft + " to go [o]");
          return;
        }
        if (trackLoop === "n") songLeft = songN; // exhausted: reset, fall through
      }
      if (pos < order.length - 1) { pos++; songLeft = songN; syncLoopUI(); load(true); return; }
      // end of queue -> playlist loop decides
      if (listLoop === "inf" || (listLoop === "n" && listLeft > 1)) {
        if (listLoop === "n") { listLeft--; syncLoopUI(); }
        restartPass();
      } else {
        if (listLoop === "n") { listLeft = listN; syncLoopUI(); }
        status("end of queue. [O] playlist-loop / [n] next");
      }
    }
    function prev() {
      if (audio.currentTime > 3) { audio.currentTime = 0; return; }
      pos = (pos - 1 + order.length) % order.length;
      songLeft = songN; syncLoopUI();
      load(true);
    }

    function setShuffleUI() {
      if (els.shuffle) {
        els.shuffle.textContent = "mix~local [s]" + (shuffleMode === "local" ? ":on" : "");
        els.shuffle.classList.toggle("on", shuffleMode === "local");
      }
      if (els.shuffleSrv) {
        els.shuffleSrv.textContent = "mix=saved [S]" + (shuffleMode === "server" ? ":on" : "");
        els.shuffleSrv.classList.toggle("on", shuffleMode === "server");
      }
    }
    function toggleLocalShuffle() {
      const curIdx = order[pos];
      if (shuffleMode === "local") {
        shuffleMode = "off";
        order = savedOrder.slice();
        status("local mix off — back to saved order");
      } else {
        shuffleMode = "local";
        order = shuffled(order);
        status("local mix on — temporary [S] makes it permanent, [w] keeps this one");
      }
      pos = order.indexOf(curIdx);
      setShuffleUI(); renderQueue(); markDirty();
    }
    async function toggleServerShuffle() {
      if (!opts.playlistId) { status("saved mix needs a playlist"); return; }
      if (shuffleMode === "server") {
        shuffleMode = "off";
        setShuffleUI(); status("saved-mix mode off (order unchanged)");
        return;
      }
      const curIdx = order[pos];
      const ids = shuffled(orderIds());
      const prevOrder = order.slice(), prevPos = pos;
      order = ids.map(idxOfId).filter((i) => i >= 0);
      if (!order.length) order = prevOrder.slice();
      pos = order.indexOf(curIdx);
      renderQueue();
      const ok = await persistOrder(ids);
      if (ok) {
        savedOrder = order.slice();
        shuffleMode = "server";
        status("shuffled order SAVED to server [S on: fresh mix every loop pass]");
      } else {
        order = prevOrder; pos = prevPos; renderQueue();
        status("server shuffle FAILED — order restored");
      }
      setShuffleUI(); markDirty();
    }

    function syncLoopUI() {
      if (els.loop) {
        els.loop.textContent = "loop-song [o]:" + (trackLoop === "off" ? "off" : trackLoop === "inf" ? "∞" : "x" + songLeft);
        els.loop.classList.toggle("on", trackLoop !== "off");
      }
      if (els.loopNSong) els.loopNSong.hidden = trackLoop !== "n";
      if (els.loopList) {
        els.loopList.textContent = "loop-list [O]:" + (listLoop === "off" ? "off" : listLoop === "inf" ? "∞" : "x" + listLeft);
        els.loopList.classList.toggle("on", listLoop !== "off");
      }
      if (els.loopN) els.loopN.hidden = listLoop !== "n";
    }
    function cycleTrackLoop() {
      if (trackLoop === "off") trackLoop = "inf";
      else if (trackLoop === "inf") { trackLoop = "n"; songLeft = songN; }
      else { trackLoop = "off"; songLeft = songN; }
      syncLoopUI();
      status("song loop=" + trackLoop + (trackLoop === "n" ? " (x" + songLeft + " total plays)" : ""));
    }
    function cycleListLoop() {
      if (listLoop === "off") { listLoop = "inf"; }
      else if (listLoop === "inf") { listLoop = "n"; listLeft = listN; }
      else { listLoop = "off"; listLeft = listN; }
      syncLoopUI(); status("playlist loop=" + listLoop + (listLoop === "n" ? " (x" + listLeft + ")" : ""));
    }

    function setSleep(mins) {
      clearTimeout(sleepTimer); sleepTimer = null;
      if (!mins || mins <= 0) { status("sleep off"); paintSleep(); return; }
      mins = Math.min(480, Math.max(1, Math.round(mins)));
      sleepAt = Date.now() + mins * 60000;
      sleepTimer = setTimeout(() => { sleepTimer = null; audio.pause(); setPlayBtn(false); status("sleep timer fired. playback stopped."); paintSleep(); }, mins * 60000);
      status("sleep in " + mins + "min");
      paintSleep();
    }
    function paintSleep() {
      if (els.sleepLeft) els.sleepLeft.textContent = "";
      if (!sleepTimer) {
        if (els.sleep && els.sleep.value !== "custom") els.sleep.value = "0";
        const custom = els.sleep && els.sleep.value === "custom";
        if (els.sleepC) els.sleepC.hidden = !custom;
        if (els.sleepSet) els.sleepSet.hidden = !custom;
        return;
      }
      const left = Math.max(1, Math.round((sleepAt - Date.now()) / 60000));
      if (els.sleepLeft) els.sleepLeft.textContent = left + "m left";
      if (els.sleep && [...els.sleep.options].some((o) => o.value === String(left))) els.sleep.value = String(left);
    }
    function currentSpeed() {
      if (els.speedC && els.speed && els.speed.value === "custom") {
        const v = parseFloat(els.speedC.value);
        return Math.min(3, Math.max(0.25, isNaN(v) ? 1 : v));
      }
      return parseFloat(els.speed ? els.speed.value : "1") || 1;
    }
    function applySpeed(v, why) {
      v = Math.min(3, Math.max(0.25, Math.round(v * 100) / 100));
      audio.playbackRate = v;
      if (els.speedC) els.speedC.value = String(v);
      status("speed " + v + "x" + (why ? " " + why : ""));
    }
    function stepSpeed(dir) {
      const s = els.speed;
      if (!s) return;
      if (s.value === "custom" && els.speedC) {
        applySpeed(currentSpeed() + dir * 0.05);
      } else {
        const choices = [...s.options].map((o) => o.value).filter((v) => v !== "custom");
        let i = choices.indexOf(s.value);
        i = Math.min(choices.length - 1, Math.max(0, (i < 0 ? choices.indexOf("1") : i) + dir));
        s.value = choices[i];
        s.dispatchEvent(new Event("change"));
      }
    }
    function paintVol() {
      if (els.volVal) els.volVal.textContent = audio.muted ? "muted" : Math.round(audio.volume * 100) + "%";
      if (els.vol && document.activeElement !== els.vol) els.vol.value = String(audio.volume);
    }

    /* wire */
    if (els.play) els.play.onclick = toggle;
    if (els.next) els.next.onclick = () => next(false);
    if (els.prev) els.prev.onclick = prev;
    if (els.shuffle) els.shuffle.onclick = toggleLocalShuffle;
    if (els.shuffleSrv) els.shuffleSrv.onclick = () => { toggleServerShuffle(); };
    if (els.loop) els.loop.onclick = cycleTrackLoop;
    if (els.loopNSong) els.loopNSong.onchange = () => {
      songN = Math.min(99, Math.max(1, parseInt(els.loopNSong.value, 10) || 2));
      els.loopNSong.value = String(songN);
      if (trackLoop === "n") { songLeft = songN; }
      syncLoopUI();
    };
    if (els.loopList) els.loopList.onclick = cycleListLoop;
    if (els.loopN) els.loopN.onchange = () => {
      listN = Math.min(99, Math.max(1, parseInt(els.loopN.value, 10) || 2));
      els.loopN.value = String(listN);
      if (listLoop === "n") { listLeft = listN; }
      syncLoopUI();
    };
    if (els.save) els.save.onclick = () => { doSave(); };
    if (els.sleep) els.sleep.onchange = () => {
      const custom = els.sleep.value === "custom";
      if (els.sleepC) els.sleepC.hidden = !custom;
      if (els.sleepSet) els.sleepSet.hidden = !custom;
      if (!custom) setSleep(parseFloat(els.sleep.value));
      else if (els.sleepC) { els.sleepC.focus(); els.sleepC.select(); }
    };
    if (els.sleepSet) els.sleepSet.onclick = () => {
      const v = els.sleepC ? parseFloat(els.sleepC.value) : NaN;
      if (!v || v <= 0) { status("sleep: type minutes first"); return; }
      setSleep(v);
    };
    if (els.mute) els.mute.onclick = () => { audio.muted = !audio.muted; els.mute.classList.toggle("on", audio.muted); paintVol(); };
    if (els.speed) els.speed.onchange = () => {
      const custom = els.speed.value === "custom";
      if (els.speedC) els.speedC.hidden = !custom;
      if (els.speedSet) els.speedSet.hidden = !custom;
      if (!custom) applySpeed(parseFloat(els.speed.value) || 1);
      else if (els.speedC) { els.speedC.focus(); els.speedC.select(); }
    };
    if (els.speedSet) els.speedSet.onclick = () => applySpeed(currentSpeed());
    if (els.vol) els.vol.oninput = () => { audio.volume = parseFloat(els.vol.value); audio.muted = false; if (els.mute) els.mute.classList.remove("on"); paintVol(); };
    audio.addEventListener("play", () => setPlayBtn(true));
    audio.addEventListener("pause", () => setPlayBtn(false));
    audio.addEventListener("ended", () => next(true));
    audio.addEventListener("timeupdate", () => {
      if (els.tCur) els.tCur.textContent = fmt(audio.currentTime);
      if (els.seek && document.activeElement !== els.seek) {
        const d = audio.duration || (cur() && cur().duration_sec) || 0;
        els.seek.value = d ? String((audio.currentTime / d) * 1000) : "0";
      }
      setPositionState();
    });
    audio.addEventListener("loadedmetadata", () => {
      if (els.tDur) els.tDur.textContent = fmt(audio.duration);
      setPositionState();
    });
    if (els.seek) {
      els.seek.addEventListener("input", () => {
        const d = audio.duration || (cur() && cur().duration_sec) || 0;
        if (d) audio.currentTime = (parseFloat(els.seek.value) / 1000) * d;
      });
    }

    /* keyboard (player owns keys when not typing) */
    document.addEventListener("keydown", (e) => {
      const tag = (document.activeElement && document.activeElement.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const k = e.key;
      const seekStep = (s) => { audio.currentTime = Math.max(0, audio.currentTime + s); };
      if (k === " " || k === "k") { e.preventDefault(); toggle(); }
      else if (k === "j") seekStep(-10);
      else if (k === "l") seekStep(10);
      else if (k === "ArrowLeft") { e.preventDefault(); seekStep(-5); }
      else if (k === "ArrowRight") { e.preventDefault(); seekStep(5); }
      else if (k === "ArrowUp") { e.preventDefault(); audio.volume = Math.min(1, audio.volume + 0.05); paintVol(); }
      else if (k === "ArrowDown") { e.preventDefault(); audio.volume = Math.max(0, audio.volume - 0.05); paintVol(); }
      else if (k === "n") next(false);
      else if (k === "p") prev();
      else if (k === "m") { audio.muted = !audio.muted; if (els.mute) els.mute.classList.toggle("on", audio.muted); paintVol(); }
      else if (k === "s") toggleLocalShuffle();
      else if (k === "S") { toggleServerShuffle(); }
      else if (k === "o") cycleTrackLoop();
      else if (k === "O") cycleListLoop();
      else if (k === "w") { doSave(); }
      else if (k === "z") { // sleep cycle: off->15->30->45->60->off
        const cur0 = els.sleep && els.sleep.value !== "custom" ? els.sleep.value : "0";
        const nxt = cur0 === "0" ? "15" : cur0 === "15" ? "30" : cur0 === "30" ? "45" : cur0 === "45" ? "60" : "0";
        if (els.sleep) { els.sleep.value = nxt; els.sleep.dispatchEvent(new Event("change")); }
        else setSleep(parseFloat(nxt));
      }
      else if (k === "[") { stepSpeed(-1); }
      else if (k === "]") { stepSpeed(1); }
      else if (/^[0-9]$/.test(k) && order.length > 1) {
        const idx = parseInt(k, 10);
        const target = idx === 0 ? 9 : idx - 1;
        if (target < order.length) { pos = target; songLeft = songN; syncLoopUI(); load(true); }
      }
    });

    syncLoopUI();
    setShuffleUI();
    markDirty();
    paintVol();
    paintSleep();
    setupMediaSession();
    load(false);
    return {
      load, next, prev, toggle, save: doSave,
      getOrderIds: orderIds,
      isDirty: () => dirty,
      setSelectMode, selectAll, getSelectedIds,
      get pos() { return pos; },
    };
  }
  window.apPlayer = initPlayer;
})();
