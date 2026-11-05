'use strict';
(() => {
  const root = document.querySelector('.facility-layout');
  const panel = document.getElementById('facility-panel');
  if (!root || !panel) return;
  const layer = root.dataset.mapLayer;
  if (typeof L === 'undefined') {
    panel.textContent = '地図ライブラリがありません。ローカルのLeafletを配置してください。';
    return;
  }
  const map = L.map('facility-map').setView([35.0, 138.0], 10);
  L.tileLayer(`/api/v1/maps/${encodeURIComponent(layer)}/{z}/{x}/{y}.png`, {
    maxZoom: 18, attribution: '庁内保管地図'
  }).addTo(map);
  let activeRequest = 0;
  const element = (tag, text, cls) => {
    const e = document.createElement(tag);
    if (text !== undefined && text !== null) e.textContent = String(text);
    if (cls) e.className = cls;
    return e;
  };
  const showError = (message) => {
    panel.replaceChildren(element('p', message));
  };
  const requestJSON = async (url) => {
    const response = await fetch(url, {credentials:'same-origin', cache:'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  };
  const row = (list, name, value) => {
    list.append(element('dt', name), element('dd', value ?? '―'));
  };
  const button = (text, action, cls) => {
    const b = element('button', text, cls);
    b.type = 'button';
    b.addEventListener('click', action);
    return b;
  };
  const detailPath = (id) => Number.isSafeInteger(id) && id > 0 ? `/facilities/${id}` : null;
  const repairPath = (id) => Number.isSafeInteger(id) && id > 0 ? `/case-repairs/repairs/${id}` : null;
  const casePath = (id) => Number.isSafeInteger(id) && id > 0 ? `/case-repairs/cases/${id}` : null;
  const navigationLink = (text, path) => {
    const anchor = element('a', text);
    if (path) anchor.href = path;
    return anchor;
  };
  const line = (title, value) => {
    const item = element('li');
    item.append(element('strong', title), element('span', ` — ${value ?? '―'}`));
    return item;
  };
  async function showSummary(id) {
    const generation = ++activeRequest;
    showError('施設情報を読み込み中…');
    try {
      const data = await requestJSON(`/api/v1/facilities/${id}/summary`);
      if (generation !== activeRequest) return;
      const f = data.facility;
      const title = element('h1', f.facility_name);
      const photo = element('div', '写真未登録・写真管理未接続', 'facility-photo');
      const definition = element('dl');
      row(definition, '分類', `${f.category_name} ＞ ${f.type_name}`);
      row(definition, '施設番号', f.facility_code);
      const counts = element('div', undefined, 'counts');
      for (const [name, count] of [
        ['修繕', data.repair_count], ['関連案件', data.related_case_count], ['未完了案件', data.active_case_count]
      ]) {
        const card = element('div');
        card.append(element('span', name), element('strong', count));
        counts.append(card);
      }
      const inspection = element('p', `最終点検：${data.last_inspection_on ?? '記録なし'}`);
      const recent = element('p');
      if (data.recent_repair) {
        recent.append('直近修繕：', navigationLink(
          data.recent_repair.repair_name, repairPath(data.recent_repair.repair_id)
        ));
      } else {
        recent.textContent = '修繕履歴なし';
      }
      const openPage = navigationLink('施設詳細ページを開く', detailPath(id));
      panel.replaceChildren(title, photo, definition, counts, inspection, recent,
        button('施設詳細を見る', () => showDetail(id, 'basic')),
        button('修繕履歴を見る', () => showDetail(id, 'repairs'), 'back'),
        openPage);
    } catch (error) {
      if (generation === activeRequest) showError(`施設情報を取得できませんでした：${error.message}`);
    }
  }
  async function showDetail(id, initialTab) {
    const generation = ++activeRequest;
    showError('詳細情報を読み込み中…');
    try {
      const data = await requestJSON(`/api/v1/facilities/${id}/detail`);
      if (generation !== activeRequest) return;
      const title = element('h1', data.facility.facility_name);
      const back = button('概要に戻る', () => showSummary(id), 'back');
      const tabs = element('nav');
      const contents = element('section');
      function activate(tab) {
        contents.replaceChildren();
        if (tab === 'basic') {
          const dl = element('dl');
          row(dl, '施設番号', data.facility.facility_code);
          row(dl, '分類', `${data.facility.category_name} ＞ ${data.facility.type_name}`);
          row(dl, '説明', data.facility.description);
          contents.append(dl);
        } else if (tab === 'attributes') {
          const dl = element('dl');
          for (const [key, value] of Object.entries(data.attributes ?? {})) row(dl, key, value);
          contents.append(dl, element('h2', '設備'));
          const ul = element('ul');
          for (const c of data.components) ul.append(line(c.component_name, c.component_type));
          contents.append(ul);
        } else {
          const mapTypes = {repairs:['修繕履歴',data.repairs,'repair_name','completed_on'],
            inspections:['点検履歴',data.inspections,'inspection_name','inspection_date'],
            cases:['関連案件',data.cases,'title','received_on']};
          const [heading, records, label, when] = mapTypes[tab];
          contents.append(element('h2', heading));
          if (!records.length) contents.append(element('p', '登録なし'));
          const ul = element('ul');
          for (const record of records) {
            if (tab === 'repairs' || tab === 'cases') {
              const li = element('li');
              const path = tab === 'repairs'
                ? repairPath(record.repair_id) : casePath(record.case_id);
              li.append(navigationLink(record[label], path),
                element('span', ` — ${record[when] ?? '―'}`));
              ul.append(li);
            } else {
              ul.append(line(record[label], record[when]));
            }
          }
          contents.append(ul);
        }
      }
      for (const [key,label] of [['basic','基本'],['attributes','機能・設備'],
        ['repairs','修繕'],['inspections','点検'],['cases','案件']]) {
        tabs.append(button(label, () => activate(key), 'back'));
      }
      panel.replaceChildren(title, back, tabs, contents);
      activate(initialTab);
    } catch (error) {
      if (generation === activeRequest) showError(`詳細情報を取得できませんでした：${error.message}`);
    }
  }
  requestJSON('/api/v1/facilities/geojson').then(data => {
    const features = L.geoJSON(data, {
      style: {color:'#2162a6', weight:4},
      pointToLayer: (feature, latlng) => L.circleMarker(latlng, {radius:8, color:'#2162a6'}),
      onEachFeature: (feature, featureLayer) => {
        featureLayer.on('click', () => showSummary(feature.properties.facility_id));
        featureLayer.bindTooltip(feature.properties.facility_name);
      }
    }).addTo(map);
    if (features.getLayers().length) map.fitBounds(features.getBounds(), {padding:[24,24], maxZoom:15});
    else showError('位置情報が登録された施設はありません。');
    if (data.omitted_invalid_geometry_count) {
      console.warn('不正な施設図形を除外:', data.omitted_invalid_geometry_count);
    }
  }).catch(e => showError(`施設位置情報を読み込めませんでした：${e.message}`));
})();
