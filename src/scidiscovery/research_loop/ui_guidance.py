"""Guidance composition UI; submission and immutable binding stay in user_guidance."""
import html


TARGETS = [('global', '所有研究角色'), ('H', 'H · 假设'), ('W', 'W · 实验'), ('A', 'A · 综合评估'), ('E', 'E · 证据')]
KINDS = [('advice', '补充建议'), ('goal_change', '明确修改研究目标')]
SCOPES = [('next', '本次适用阶段'), ('research', '本次及研究的后续适用阶段')]


def state(row, *, manager):
    """Project public queue status from one already acquired guidance original."""
    claim = row.get('turn_input') or {}
    status = claim.get('status')
    labels = {'queued': '已加入当前 turn 待发队列', 'submitting': '正在加入当前 turn 待发队列',
        'accepted': '已加入当前 turn 待发队列', 'unknown': '当前 turn 入队结果待核对'}
    label = labels.get(status, {'pending': '等待适用阶段', 'applied': '已绑定适用阶段',
        'revoked': '已撤回'}.get(row['status'], row['status']))
    if claim:
        note = ('绑定第 '+str(claim.get('step', '?'))+' 阶段 '+str(claim.get('role', '?'))+' 的当前 turn；'
            + ('入队结果未知，保留原调用核对，不自动重发。' if status=='unknown' else
               '等待后端在当前 turn 的原生边界处理，不中断当前工作。')
            + '入队不表示模型已读取、采纳或形成科学结论。'
            + ('研究范围的撤回只影响未来阶段，无法撤回已经入队的当前消息。' if row.get('scope')=='research' else ''))
    elif row['status']=='applied':
        note = '已绑定仅表示建议进入研究任务，不表示科学结论已采纳，也不表示模型已读取或采用。'
    elif row['status']=='pending':
        note = '等待匹配角色的适用阶段；若该角色已有可用当前 turn，则加入其待发队列。明确的未来生效阶段不会提前交付。'
    else:
        note = '后续适用范围已撤回；已经绑定的任务和当前 turn 消息保持原样。'
    if row.get('last_turn_input') and not claim and row['status']=='pending':
        note = '原当前 turn 消息有未提交证明，已解除绑定；'+note
    if row.get('future_revocation'):
        note += '已撤回后续建议：从第 '+str(row['future_revocation']['effective_step'])+' 阶段起不再用于新任务；已经绑定的任务和当前 turn 消息保持原样。'
    return dict(guidance_id=row['guidance_id'], label=label, note=note,
        binding='第 '+str(row['applied_step'])+' 阶段' if row.get('applied_step') else '尚未绑定',
        editable=bool(manager and row['status']=='pending' and not claim),
        revocable=bool(manager and (row['status']=='pending' and not claim or
            row['status']=='applied' and row.get('scope')=='research' and not row.get('future_revocation'))))


def fields(draft=None):
    draft = {**dict(text='', target='global', kind='advice', scope='next', effective_step=''), **(draft or {})}

    def select(name, label, choices):
        value = draft[name]
        options = list(choices)
        if value not in dict(options):
            options.append((value, '无效选项（请重新选择）'))
        return '<label>' + label + '<select name="' + name + '">' + ''.join(
            '<option value="' + html.escape(key, quote=True) + '"' + (' selected' if value == key else '')
            + '>' + label + '</option>' for key, label in options) + '</select></label>'

    templates = [
        ('检查证据', '请检查当前结论的证据是否充分，指出缺失证据和仍需验证的问题。'),
        ('比较替代方案', '请比较当前方案与可行替代方案，说明选择依据、代价和仍不确定的部分。'),
        ('调整关注点', '请优先关注【填写关注点】，并说明它会如何影响后续判断。'),
    ]
    return ('<div class="guidance-composer" data-guidance-composer>'
        '<p class="note">从常见意图开始，或直接写下你的要求。模板只填入空白草稿，不会自动提交。</p>'
        '<div class="guidance-templates">' + ''.join('<button type="button" data-guidance-template="'
            + html.escape(text, quote=True) + '">' + label + '</button>' for label, text in templates) + '</div>'
        '<p class="note" data-guidance-template-status role="status"></p>'
        '<label>你希望研究者关注什么？<textarea name="text" rows="5" required placeholder="写清关注点、希望比较的选项，或还缺少的证据。">'
        + html.escape(draft['text']) + '</textarea></label>'
        + select('kind', '这次提交的意图', KINDS)
        + '<p class="guidance-goal-note" data-guidance-goal-note' + ('' if draft['kind'] == 'goal_change' else ' hidden')
        + '>修改目标会面向所有研究角色，并持续用于后续适用阶段。请明确写出新目标和生效阶段；原始目标仍保留。</p>'
        '<details class="guidance-advanced"' + (' open' if draft['kind'] == 'goal_change' or draft['target'] != 'global'
            or draft['scope'] != 'next' or draft['effective_step'] else '') + '><summary>指定角色与生效范围</summary>'
        '<p class="note">目标角色正在运行时，加入其当前 turn 待发队列，不中断当前工作；global 匹配当前角色。目标角色未运行时等待下一适用阶段，明确的未来生效阶段不会提前交付。</p>'
        '<div class="guidance-fields">' + select('target', '交给谁', TARGETS) + select('scope', '持续多久', SCOPES)
        + '<label>最早生效阶段<input name="effective_step" type="number" min="1" step="1" value="'
        + html.escape(str(draft['effective_step']), quote=True) + '"' + (' required' if draft['kind'] == 'goal_change' else '')
        + '><span class="note">补充建议可留空；修改目标必须填写正整数并保留显式未来阶段合同。不会改写原任务或提前交付。</span></label></div></details></div>')


SCRIPT = """(() => {
  for (const composer of document.querySelectorAll('[data-guidance-composer]')) {
    const form = composer.closest('form');
    const text = form.elements.namedItem('text');
    const kind = form.elements.namedItem('kind');
    const target = form.elements.namedItem('target');
    const scope = form.elements.namedItem('scope');
    const step = form.elements.namedItem('effective_step');
    for (const button of composer.querySelectorAll('[data-guidance-template]')) {
      button.addEventListener('click', () => {
        const status = composer.querySelector('[data-guidance-template-status]');
        if (text.value.trim()) {
          status.textContent = '已保留你写下的草稿。清空后可选择模板。';
        } else {
          text.value = button.dataset.guidanceTemplate;
          text.dispatchEvent(new Event('input', {bubbles:true}));
          status.textContent = '模板已填入草稿，请修改后提交。';
        }
        text.focus();
      });
    }
    const update = (changed) => {
      const goal = kind.value === 'goal_change';
      if (goal && changed) { target.value = 'global'; scope.value = 'research'; }
      for (const option of target.options) option.disabled = goal && option.value !== 'global';
      for (const option of scope.options) option.disabled = goal && option.value !== 'research';
      step.required = goal;
      composer.querySelector('[data-guidance-goal-note]').hidden = !goal;
      if (goal) composer.querySelector('details').open = true;
    };
    kind.addEventListener('change', () => update(true));
    update(false);
  }
  const history = document.querySelector('[data-guidance-history]');
  if (history) {
    let timer;
    const poll = async () => {
      if (!document.hidden) {
        try {
          const response = await fetch(history.dataset.statusUrl, {credentials:'same-origin', cache:'no-store'});
          if (response.ok) {
            const data = await response.json();
            for (const row of data.rows) {
              const record = Array.from(history.querySelectorAll('[data-guidance-id]')).find(item => item.dataset.guidanceId === row.guidance_id);
              if (!record) continue;
              record.querySelector('[data-guidance-status]').textContent = row.label;
              record.querySelector('[data-guidance-delivery-note]').textContent = row.note;
              record.querySelector('[data-guidance-binding]').textContent = row.binding;
              const edit = record.querySelector('[data-guidance-edit]');
              const revoke = record.querySelector('[data-guidance-revoke]');
              if (edit) edit.hidden = !row.editable;
              if (revoke) revoke.hidden = !row.revocable;
            }
          }
        } catch (_) { /* Keep the last observed state and every unsaved draft. */ }
      }
      timer = setTimeout(poll, 2000);
    };
    timer = setTimeout(poll, 2000);
    window.addEventListener('pagehide', () => clearTimeout(timer), {once:true});
  }
})();"""
