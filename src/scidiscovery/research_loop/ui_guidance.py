"""Guidance composition UI; submission and immutable binding stay in user_guidance."""
import html


TARGETS = [('global', '所有研究角色'), ('H', 'H · 假设'), ('W', 'W · 实验'), ('A', 'A · 综合评估'), ('E', 'E · 证据')]
KINDS = [('advice', '补充建议'), ('goal_change', '明确修改研究目标')]
SCOPES = [('next', '下一适用阶段'), ('research', '研究的后续适用阶段')]


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
        '<p class="note">默认交给所有角色中的下一适用阶段；不会改变已经绑定的任务。指定角色时，等待该角色的下一适用阶段。</p>'
        '<div class="guidance-fields">' + select('target', '交给谁', TARGETS) + select('scope', '持续多久', SCOPES)
        + '<label>最早生效阶段<input name="effective_step" type="number" min="1" step="1" value="'
        + html.escape(str(draft['effective_step']), quote=True) + '"' + (' required' if draft['kind'] == 'goal_change' else '')
        + '><span class="note">补充建议可留空；修改目标必须填写正整数。已经绑定的阶段不会改写。</span></label></div></details></div>')


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
})();"""
