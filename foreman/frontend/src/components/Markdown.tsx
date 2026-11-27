import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * Renders agent-written Markdown. Raw HTML is not interpreted, and images are not loaded: a
 * poisoned document could otherwise leak data through an image URL the moment someone reads it.
 */
export default function Markdown({ children }: { children: string }) {
  return (
    <div className="prose prose-invert prose-sm max-w-none break-words overflow-x-auto">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        disallowedElements={['img']}
        components={{
          a: ({ href, children: label }) => (
            // the address is shown too, so a link cannot pretend to go somewhere else
            <a href={href} target="_blank" rel="noreferrer noopener">
              {label} <span className="text-slate-500">({href})</span>
            </a>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
