(request) => {
  const visible = el => !!el && getComputedStyle(el).visibility !== 'hidden' &&
    getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0;
  const text = el => (el ? (el.innerText || el.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const norm = s => String(s || '').replace(/[\s＊*：:]/g, '');
  const tag = (el, name) => { document.querySelectorAll(`[data-nccbot-${name}]`).forEach(x => x.removeAttribute(`data-nccbot-${name}`)); el.setAttribute(`data-nccbot-${name}`, '1'); };
  if (request.action === 'field') {
    const names = request.names.map(norm);
    const inputs = [...document.querySelectorAll('input')].filter(visible);
    for (const input of inputs) {
      const own = [input.placeholder, input.getAttribute('label'), input.getAttribute('aria-label'), input.title].map(norm);
      let box = input.closest('.form-item, .u-form-item, .ant-form-item, [class*="search-item"], [class*="searchItem"]');
      const labels = box ? [...box.querySelectorAll('label, [class*="label"], [class*="title"]')].map(text).map(norm) : [];
      if (names.some(name => own.includes(name) || labels.includes(name))) {
        tag(box || input.parentElement, 'field'); tag(input, 'input');
        return true;
      }
    }
    return false;
  }
  if (request.action === 'businessDate') {
    const inCalendar=el=>!!el.closest('.rc-calendar, [class*="fieldid_business-date_calendar"], [class*="datepicker-panel"], [class*="calendar-panel"]');
    const official = [...document.querySelectorAll('.nc-workbench-businessdate-dropdown input')].filter(visible)
      .filter(el=>el.readOnly && !inCalendar(el));
    if(official.length===1 && /^\d{4}-\d{1,2}-\d{1,2}$/.test(official[0].value)) {
      tag(official[0],'business-date'); return official[0].value;
    }
    const candidates = [...document.querySelectorAll('input, span, div, a, button')].filter(el => {
      const r = el.getBoundingClientRect();
      return visible(el) && !inCalendar(el) && (!el.matches('input') || el.readOnly) &&
        r.top >= 0 && r.top < 65 && r.left > innerWidth * .65 &&
        /^\d{4}-\d{1,2}-\d{1,2}$/.test(el.value || text(el));
    }).sort((a,b) => a.getBoundingClientRect().width-b.getBoundingClientRect().width);
    if (!candidates.length) return null;
    const el = candidates[0]; tag(el, 'business-date'); return el.value || text(el);
  }
  if (request.action === 'businessDateState') {
    const official=[...document.querySelectorAll('[class*="fieldid_business-date_calendar"]')].filter(visible);
    const fallback=[...document.querySelectorAll('.rc-calendar')].filter(visible)
      .filter(el=>[...el.querySelectorAll('input.rc-calendar-input,input[placeholder="YYYYMMDD"]')].some(visible));
    const panels=official.length ? official : fallback;
    const masks=[...document.querySelectorAll('.business-date-mask')].filter(visible)
      .filter(el=>getComputedStyle(el).pointerEvents!=='none');
    ['business-date-panel','business-date-editor','business-date-confirm'].forEach(name=>
      document.querySelectorAll(`[data-nccbot-${name}]`).forEach(el=>el.removeAttribute(`data-nccbot-${name}`)));
    let editor=null, confirm=null;
    if(panels.length===1) {
      const panel=panels[0]; tag(panel,'business-date-panel');
      const editors=[...panel.querySelectorAll('input.rc-calendar-input,input[placeholder="YYYYMMDD"],input:not([readonly])')]
        .filter(visible).filter(el=>!el.readOnly);
      if(editors.length===1) {editor=editors[0];tag(editor,'business-date-editor');}
      const confirms=[...panel.querySelectorAll('button,a,span,[role="button"]')]
        .filter(visible).filter(el=>/^(确定|确认|应用)$/.test(text(el)))
        .filter(el=>![...el.querySelectorAll('button,a,span,[role="button"]')].some(child=>visible(child)&&/^(确定|确认|应用)$/.test(text(child))));
      if(confirms.length===1) {confirm=confirms[0];tag(confirm,'business-date-confirm');}
    }
    return {popup_visible:panels.length>0,mask_visible:masks.length>0,
      confirm_available:!!confirm,editor_available:!!editor,ambiguous:panels.length>1};
  }
  if (request.action === 'businessDateClickTarget') {
    const input=document.querySelector('[data-nccbot-business-date]');
    if(!input) return false;
    const rect=input.getBoundingClientRect(), x=rect.left+rect.width/2, y=rect.top+rect.height/2;
    const blocks=[...document.querySelectorAll('.business-date-block')].filter(visible).filter(el=>{
      const r=el.getBoundingClientRect(); return r.left<=x && r.right>=x && r.top<=y && r.bottom>=y;
    });
    if(blocks.length>1) return false;
    // NCC intentionally places this click surface above the readonly input.
    // Click it normally instead of forcing through/removing the overlay.
    tag(blocks[0] || input,'business-date-trigger'); return true;
  }
  if (request.action === 'grid') {
    const wrappers = [...document.querySelectorAll('.u-table, .nc-table, .ant-table, [role="grid"]')].filter(visible);
    let grid = wrappers.map(el => ({el, score: ['签字人','业务单据编号','结算方式','财务组织名称'].filter(s=>text(el).includes(s)).length}))
      .sort((a,b)=>b.score-a.score || text(a.el).length-text(b.el).length)[0];
    if (!grid || grid.score < 1) return {error:'未找到结算明细表格'};
    grid = grid.el; tag(grid, 'grid');
    const tables = [...grid.querySelectorAll('table')].filter(visible);
    const tableHeaders = table => [...table.querySelectorAll('thead tr:last-child th')]
      .filter(th=>th.closest('table')===table).map(text);
    const sameHeaders = (a,b) => JSON.stringify(a) === JSON.stringify(b);
    const headersForBody = (table, count) => {
      const own = tableHeaders(table);
      if (own.length) return own.length === count ? own : null;
      // rc-table puts header/body in sibling containers inside each scroll/fixed
      // section. Search the nearest shared ancestor, never jump to another fixed
      // section, and accept only one unambiguous header map of the same width.
      const section = table.closest('.u-table-scroll, .u-table-fixed-left, .u-table-fixed-right, .ant-table-scroll, .ant-table-fixed-left, .ant-table-fixed-right');
      for (let box=table.parentElement; box; box=box.parentElement) {
        const candidates = [...box.querySelectorAll('table')].filter(candidate=>candidate!==table && visible(candidate))
          .filter(candidate=>!section || candidate.closest('.u-table-scroll, .u-table-fixed-left, .u-table-fixed-right, .ant-table-scroll, .ant-table-fixed-left, .ant-table-fixed-right')===section)
          .map(tableHeaders).filter(headers=>headers.length===count);
        if (candidates.length) return candidates.every(h=>sameHeaders(h,candidates[0])) ? candidates[0] : null;
        if (box===section || box===grid) break;
      }
      return null;
    };
    const pieces = []; let conflict = false;
    for (const table of tables) {
      const trs = [...table.querySelectorAll('tbody tr')].filter(tr=>tr.closest('table')===table &&
        tr.querySelector(':scope > td') && !/measure/.test(tr.className));
      if (!trs.length) continue;
      const count = trs[0].querySelectorAll(':scope > td').length;
      const headers = headersForBody(table,count);
      if (!headers) continue;
      const rows = [];
      for (const tr of trs) {
        const cells = [...tr.querySelectorAll(':scope > td')];
        if(cells.length!==headers.length) { conflict=true; continue; }
        const values = {};
        headers.forEach((h,i)=>{
          if(!h) return;
          const value=text(cells[i]);
          if(values[h] && value && values[h]!==value) conflict=true;
          if(value || !(h in values)) values[h]=value;
        });
        const dom_key = (tr.getAttribute('data-row-key') || '').trim();
        // Summary tables have neither a real row key nor a business bill number.
        if(!dom_key && !(values['业务单据编号'] || '').trim()) continue;
        const cb=tr.querySelector('input[type="checkbox"], [role="checkbox"]');
        const custom=cb && cb.closest('.nc-checkbox');
        const checked=!!cb && (custom ? custom.classList.contains('is-checked') : cb.matches('input') ? cb.checked===true : cb.getAttribute('aria-checked')==='true');
        rows.push({tr,values,dom_key,checkbox:!!cb,checked});
      }
      pieces.push({table,headers,rows});
    }
    if (!pieces.length) return {error:'未识别到表头及表体，禁止猜测列位置'};
    const complete = pieces.filter(piece=>piece.headers.includes('业务单据编号') && piece.headers.includes('签字人') &&
      (piece.headers.includes('签字确认日期') || piece.headers.includes('签字日期')) && piece.rows.length)
      .sort((a,b)=>Number(!!b.table.closest('.u-table-scroll,.ant-table-scroll'))-Number(!!a.table.closest('.u-table-scroll,.ant-table-scroll')) || b.headers.length-a.headers.length);
    const primary = complete[0];
    if (complete.length>1 && !primary.table.closest('.u-table-scroll,.ant-table-scroll') &&
        complete[1].headers.length===primary.headers.length && complete.some(piece=>piece.rows.some(row=>!row.dom_key))) {
      return {error:'多个完整表体缺少唯一行标识，禁止猜测对应关系'};
    }
    const merged = new Map();
    for (const piece of pieces) {
      const seenKeys = new Set();
      for (const row of piece.rows) {
        // Keyless rows are permitted only in a complete primary table. Their
        // physical positions are never used to join another table's columns.
        if(!row.dom_key && piece!==primary) continue;
        const identity=row.dom_key ? 'key:'+row.dom_key : 'primary:'+piece.rows.indexOf(row);
        if(seenKeys.has(identity)) conflict=true;
        seenKeys.add(identity);
        const dest=merged.get(identity) || {values:{},dom_key:row.dom_key,checkbox:false,checked:false,trs:[]};
        for(const [k,v] of Object.entries(row.values)) {
          if(dest.values[k] && v && dest.values[k]!==v) conflict=true;
          if(v || !(k in dest.values)) dest.values[k]=v;
        }
        dest.checkbox ||= row.checkbox; dest.checked ||= row.checked;
        dest.trs.push(row.tr); merged.set(identity,dest);
      }
    }
    grid.querySelectorAll('[data-nccbot-row],[data-nccbot-token]').forEach(tr=>{
      tr.removeAttribute('data-nccbot-row'); tr.removeAttribute('data-nccbot-token');
    });
    const rows=[...merged.values()].filter(row=>(row.values['业务单据编号'] || '').trim()).map((row,index)=>{
      const token='row-'+index;
      row.trs.forEach(tr=>{tr.setAttribute('data-nccbot-row',String(index));tr.setAttribute('data-nccbot-token',token);});
      return {index,token,dom_key:row.dom_key,values:row.values,checkbox:row.checkbox,checked:row.checked};
    });
    return {headers:[...new Set(pieces.flatMap(p=>p.headers))],rows,conflict};
  }
  if (request.action === 'scrollX') {
    const grid = document.querySelector('[data-nccbot-grid]'); if(!grid) return false;
    const els = [grid, ...grid.querySelectorAll('*')].filter(el=>el.scrollWidth>el.clientWidth+30 && el.clientWidth>100);
    els.forEach(el => {el.scrollLeft=request.end ? el.scrollWidth : 0; el.dispatchEvent(new Event('scroll',{bubbles:true}));});
    return els.length > 0;
  }
  if (request.action === 'scrollY') {
    const grid=document.querySelector('[data-nccbot-grid]'); if(!grid) return {moved:false,atEnd:true};
    const els=[...grid.querySelectorAll('.u-table-scroll .u-table-body,.ant-table-scroll .ant-table-body,.u-table-body,.ant-table-body')]
      .filter(el=>visible(el) && el.scrollHeight>el.clientHeight+2 && el.clientHeight>50)
      .sort((a,b)=>b.scrollHeight-a.scrollHeight);
    const el=els[0]; if(!el) return {moved:false,atEnd:true};
    const before=el.scrollTop;
    el.scrollTop=request.start ? 0 : Math.min(el.scrollHeight,el.scrollTop+Math.max(100,el.clientHeight*.75));
    el.dispatchEvent(new Event('scroll',{bubbles:true}));
    return {moved:el.scrollTop!==before,atEnd:el.scrollTop+el.clientHeight>=el.scrollHeight-2};
  }
  if (request.action === 'pager') {
    const official = [...document.querySelectorAll('.simpleTable-component-paginationDiv')].filter(visible);
    if(official.length>1) return {total:null,page:null,size:null,pages:null};
    const scope = official[0] || document.body;
    const pagers = [...scope.querySelectorAll('[class*="pagination"], [class*="Pagination"]')].filter(visible);
    const body = text(scope);
    const totalMatch = body.match(/共\s*([\d,]+)\s*条/);
    const total = totalMatch ? Number(totalMatch[1].replaceAll(',','')) : null;
    const active = pagers.flatMap(el=>[...el.querySelectorAll('[aria-current="page"], .active, .u-pagination-item-active, .ant-pagination-item-active')]).find(el=>/^\d+$/.test(text(el)));
    const page = active ? Number(text(active)) : (total !== null && total <= 100 ? 1 : null);
    const sizes = [...scope.querySelectorAll('button, input, select, [class*="select"], [class*="Select"]')].filter(visible)
      .filter(el=>/^\d+\s*条\s*\/\s*页$/.test(el.value || text(el))).sort((a,b)=>a.getBoundingClientRect().width-b.getBoundingClientRect().width);
    const sizeEl = sizes[0];
    if(sizeEl) tag(sizeEl,'page-size');
    const size = sizeEl ? Number((sizeEl.value || text(sizeEl)).match(/\d+/)[0]) : null;
    return {total, page, size, pages:total === null ? null : Math.max(1,Math.ceil(total/100))};
  }
  if (request.action === 'pageButton') {
    const candidates = [...document.querySelectorAll('[class*="pagination"] li, [class*="pagination"] button, [class*="Pagination"] li, [class*="Pagination"] button')]
      .filter(visible).filter(el=>text(el) === String(request.number));
    if(!candidates.length) return false; tag(candidates[0],'page-button'); return true;
  }
  if (request.action === 'nextButton') {
    const actual = [...document.querySelectorAll('.u-pagination span[aria-label="Next"]')].filter(visible).map(el=>el.closest('li'));
    const candidates = actual.concat([...document.querySelectorAll('[class*="pagination"] [class*="next"], [class*="Pagination"] [class*="next"], [aria-label="下一页"], [title="下一页"]')].filter(visible));
    const el = candidates.find(el=>!el.disabled && el.getAttribute('aria-disabled')!=='true' && !/disabled/.test(el.className));
    if(!el) return false; tag(el,'page-button'); return true;
  }
  return null;
}
