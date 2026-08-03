import { useState } from 'react';

export default function TagInput({
  tags = [],
  onChange,
  onPendingChange,
  placeholder = 'Add tag...'
}) {
  const [input, setInput] = useState('');

  const parseTags = (text) => {
    if (!text) return [];
    return text
      .split(/[,;\n]+/)
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
  };

  const updateInput = (val) => {
    setInput(val);
    if (onPendingChange) {
      onPendingChange(val);
    }
  };

  const addTagsFromText = (text, currentTags = tags) => {
    const newItems = parseTags(text);
    if (newItems.length === 0) return currentTags;

    const updatedTags = [...currentTags];
    let changed = false;

    for (const item of newItems) {
      const exists = updatedTags.some(
        (t) => t.toLowerCase() === item.toLowerCase()
      );
      if (!exists) {
        updatedTags.push(item);
        changed = true;
      }
    }

    if (changed && onChange) {
      onChange(updatedTags);
    }
    return updatedTags;
  };

  const handleChange = (e) => {
    const val = e.target.value;

    if (val.includes(',') || val.includes(';') || val.includes('\n')) {
      const parts = val.split(/[,;\n]+/);
      const completeParts = parts.slice(0, -1).join(',');
      const remainder = parts[parts.length - 1];

      addTagsFromText(completeParts);
      updateInput(remainder);
    } else {
      updateInput(val);
    }
  };

  const handlePaste = (e) => {
    const pastedText = e.clipboardData?.getData('text');
    if (pastedText && (pastedText.includes(',') || pastedText.includes(';') || pastedText.includes('\n'))) {
      e.preventDefault();
      addTagsFromText(pastedText);
      updateInput('');
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ',' || e.key === 'Tab') {
      if (input.trim()) {
        e.preventDefault();
        addTagsFromText(input);
        updateInput('');
      }
    } else if (e.key === 'Backspace' && !input && tags.length > 0) {
      if (onChange) {
        onChange(tags.slice(0, -1));
      }
    }
  };

  const handleBlur = () => {
    if (input.trim()) {
      addTagsFromText(input);
      updateInput('');
    }
  };

  const removeTag = (tagToRemove) => {
    if (onChange) {
      onChange(tags.filter((t) => t !== tagToRemove));
    }
  };

  return (
    <div className="flex flex-wrap gap-2 rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 focus-within:ring-2 focus-within:ring-accent-500/50">
      {tags.map((tag) => (
        <span
          key={tag}
          className="inline-flex items-center gap-1 rounded-md bg-accent-500/15 px-2.5 py-1 text-sm text-accent-300 ring-1 ring-inset ring-accent-500/20"
        >
          {tag}
          <button
            type="button"
            onClick={() => removeTag(tag)}
            className="ml-1 text-accent-400 hover:text-accent-200"
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </span>
      ))}
      <input
        type="text"
        value={input}
        onChange={handleChange}
        onPaste={handlePaste}
        onKeyDown={handleKeyDown}
        onBlur={handleBlur}
        placeholder={tags.length === 0 ? placeholder : ''}
        className="min-w-[140px] flex-1 bg-transparent text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none"
      />
    </div>
  );
}
