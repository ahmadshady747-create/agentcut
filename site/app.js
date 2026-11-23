(function () {
  const FALLBACK_VIDEOS = [
  {
    "id": "what-is-agentcut",
    "title": "ما هو أجينت كات؟",
    "description": "جولة سريعة تشرح كيف يحوّل AgentCut الفكرة النصية إلى فيديو وثائقي مكتمل بالسرد الصوتي والترجمة والموسيقى دون مونتاج يدوي.",
    "duration": "3:08",
    "category": "شروحات",
    "thumbnail": "thumbnails/what-is-agentcut.svg",
    "type": "mp4",
    "source": "videos/what-is-agentcut.mp4"
  },
  {
    "id": "ai-race-2026",
    "title": "سباق الذكاء الاصطناعي 2026",
    "description": "وثائقي تحليلي يرصد التحولات الكبرى في نماذج الذكاء الاصطناعي والوكلاء المستقلين خلال عام 2026، أُنتج بالكامل عبر AgentCut.",
    "duration": "10:00",
    "category": "شروحات",
    "thumbnail": "thumbnails/ai-race-2026.svg",
    "type": "mp4",
    "source": "videos/ai-race-2026.mp4"
  },
  {
    "id": "what-is-inflation",
    "title": "ما هو التضخم؟",
    "description": "شرح قصير ومكثف بصيغة ريلز يبسّط مفهوم التضخم الاقتصادي وتأثيره اليومي على القوة الشرائية في أقل من دقيقة.",
    "duration": "0:55",
    "category": "ريلز",
    "thumbnail": "thumbnails/what-is-inflation.svg",
    "type": "mp4",
    "source": "videos/what-is-inflation.mp4"
  }
];

  const CATEGORIES = ["الكل", "شروحات", "ريلز", "اختبارات"];
  let allVideos = [];
  let activeCategory = "الكل";

  const filterBar = document.getElementById("filterBar");
  const videoGrid = document.getElementById("videoGrid");
  const videoCount = document.getElementById("videoCount");
  const playerModal = document.getElementById("playerModal");
  const modalTitle = document.getElementById("modalTitle");
  const modalDesc = document.getElementById("modalDesc");
  const modalMeta = document.getElementById("modalMeta");
  const playerStage = document.getElementById("playerStage");
  const closeModalBtn = document.getElementById("closeModalBtn");

  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function resolveThumb(video) {
    if (video.thumbnail && String(video.thumbnail).trim()) {
      return video.thumbnail;
    }
    if (video.type === "youtube" && video.source && String(video.source).trim()) {
      return "https://i.ytimg.com/vi/" + encodeURIComponent(video.source.trim()) + "/hqdefault.jpg";
    }
    return "thumbnails/what-is-agentcut.svg";
  }

  function renderFilters() {
    filterBar.innerHTML = CATEGORIES.map(function (cat) {
      const cls = cat === activeCategory ? "filter-btn active" : "filter-btn";
      return '<button type="button" class="' + cls + '" data-cat="' + escapeHtml(cat) + '">' + escapeHtml(cat) + "</button>";
    }).join("");

    filterBar.querySelectorAll(".filter-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        activeCategory = btn.getAttribute("data-cat") || "الكل";
        renderFilters();
        renderGallery();
      });
    });
  }

  function renderGallery() {
    const filtered = activeCategory === "الكل"
      ? allVideos
      : allVideos.filter(function (v) { return v.category === activeCategory; });

    videoCount.textContent = filtered.length === 1 ? "فيديو واحد" : filtered.length + " فيديوهات";

    if (!filtered.length) {
      videoGrid.innerHTML = '<div class="empty-state">لا توجد فيديوهات في تصنيف «' + escapeHtml(activeCategory) + '» حالياً.</div>';
      return;
    }

    videoGrid.innerHTML = filtered.map(function (video) {
      const thumbSrc = resolveThumb(video);
      return (
        '<article class="video-card" tabindex="0" role="button" data-video-id="' + escapeHtml(video.id) + '" aria-label="تشغيل ' + escapeHtml(video.title) + '">' +
          '<div class="thumb-wrap">' +
            '<img class="thumb-img" src="' + escapeHtml(thumbSrc) + '" alt="' + escapeHtml(video.title) + '" loading="lazy" />' +
            '<div class="play-overlay">' +
              '<span class="play-circle" aria-hidden="true">' +
                '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><polygon points="7 4 20 12 7 20 7 4"/></svg>' +
              "</span>" +
            "</div>" +
            '<span class="duration-badge">' + escapeHtml(video.duration || "0:00") + "</span>" +
          "</div>" +
          '<div class="card-body">' +
            '<div class="card-meta">' +
              '<span class="cat-badge">' + escapeHtml(video.category || "شروحات") + "</span>" +
            "</div>" +
            '<h3 class="card-title">' + escapeHtml(video.title) + "</h3>" +
            '<p class="card-desc">' + escapeHtml(video.description) + "</p>" +
            '<button type="button" class="card-play-btn" data-play-id="' + escapeHtml(video.id) + '">' +
              '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><polygon points="6 4 20 12 6 20 6 4"/></svg>' +
              "<span>تشغيل الفيديو</span>" +
            "</button>" +
          "</div>" +
        "</article>"
      );
    }).join("");

    videoGrid.querySelectorAll(".video-card").forEach(function (card) {
      const vidId = card.getAttribute("data-video-id");
      card.addEventListener("click", function () { openPlayer(vidId); });
      card.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openPlayer(vidId);
        }
      });
    });
  }

  function openPlayer(videoId) {
    const video = allVideos.find(function (v) { return v.id === videoId; });
    if (!video) return;

    modalTitle.textContent = video.title || "";
    modalDesc.textContent = video.description || "";
    modalMeta.textContent = (video.category || "") + " • " + (video.duration || "");

    const src = String(video.source || "").trim();
    if (!src) {
      playerStage.innerHTML =
        '<div class="player-empty">' +
          "<h3>الفيديو غير مرتبط بمصدر تشغيل بعد</h3>" +
          "<p>أضف معرّف فيديو يوتيوب أو رابط ملف MP4 مباشر في حقل <strong>source</strong> داخل ملف <strong>videos.json</strong> ليبدأ التشغيل هنا تلقائياً.</p>" +
          '<code>"id": "' + escapeHtml(video.id) + '", "type": "' + escapeHtml(video.type || "youtube") + '", "source": "..."</code>' +
        "</div>";
    } else if (video.type === "mp4") {
      playerStage.innerHTML =
        '<video controls autoplay playsinline src="' + escapeHtml(src) + '">متصفحك لا يدعم تشغيل فيديو MP4.</video>';
      var vidEl = playerStage.querySelector("video");
      if (vidEl) {
        vidEl.addEventListener("error", function () {
          playerStage.innerHTML =
            '<div class="player-empty"><h3>تعذّر تشغيل هذا الفيديو</h3>' +
            "<p>ملف الفيديو غير متوفر في هذا النشر. أضف ملف MP4 إلى مجلد <strong>videos/</strong> بالاسم المذكور في <strong>videos.json</strong>.</p></div>";
        });
      }
    } else {
      const embedUrl = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(src) + "?autoplay=1&rel=0";
      playerStage.innerHTML =
        '<iframe src="' + embedUrl + '" title="' + escapeHtml(video.title) + '" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe>';
    }

    playerModal.classList.add("open");
    document.body.style.overflow = "hidden";
    closeModalBtn.focus();
  }

  function closePlayer() {
    playerModal.classList.remove("open");
    playerStage.innerHTML = "";
    document.body.style.overflow = "";
  }

  closeModalBtn.addEventListener("click", closePlayer);
  playerModal.addEventListener("click", function (e) {
    if (e.target === playerModal) closePlayer();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && playerModal.classList.contains("open")) {
      closePlayer();
    }
  });

  function loadVideos() {
    if (typeof window.__INLINE_VIDEOS__ !== "undefined" && Array.isArray(window.__INLINE_VIDEOS__)) {
      allVideos = window.__INLINE_VIDEOS__;
      renderFilters();
      renderGallery();
      return;
    }
    if (window.location.protocol === "file:") {
      allVideos = FALLBACK_VIDEOS;
      renderFilters();
      renderGallery();
      return;
    }
    fetch("videos.json")
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        allVideos = Array.isArray(data) ? data : FALLBACK_VIDEOS;
        renderFilters();
        renderGallery();
      })
      .catch(function () {
        allVideos = FALLBACK_VIDEOS;
        renderFilters();
        renderGallery();
      });
  }

  loadVideos();
})();
