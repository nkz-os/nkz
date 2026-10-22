import React from 'react';

export type NkzAttributionVariant = 'oss' | 'commercial' | 'core';

const PROJECT_URL = 'https://nkz-os.org/';

/** Operating company, injected at runtime by entrypoint.sh; both values must be non-empty. */
function readOperator(): { url: string; name: string } | null {
  if (typeof window === 'undefined') return null;
  const env = window.__ENV__;
  const url = typeof env?.COMPANY_URL === 'string' ? env.COMPANY_URL.trim() : '';
  const name = typeof env?.COMPANY_NAME === 'string' ? env.COMPANY_NAME.trim() : '';
  if (!url || !name || !/^https?:\/\//i.test(url)) return null;
  return { url, name };
}

export const NkzAttribution: React.FC<{
  variant: NkzAttributionVariant;
  className?: string;
}> = ({ variant, className = '' }) => {
  const link = (
    <a
      href={PROJECT_URL}
      target="_blank"
      rel="noopener noreferrer"
      className="underline hover:opacity-80"
    >
      nkz-os.org
    </a>
  );

  const operator = readOperator();
  const operatedBy = operator ? (
    <>
      {' '}
      Operated by{' '}
      <a
        href={operator.url}
        target="_blank"
        rel="noopener noreferrer"
        className="underline hover:opacity-80"
      >
        {operator.name}
      </a>
      .
    </>
  ) : null;

  let text: React.ReactNode;
  switch (variant) {
    case 'oss':
      text = (
        <>
          Powered by NKZ OS. Licensed under AGPL by {link}.{operatedBy}
        </>
      );
      break;
    case 'commercial':
      text = (
        <>
          Powered by FIWARE & NKZ OS (AGPL). Built by {link}.{operatedBy}
        </>
      );
      break;
    case 'core':
    default:
      text = (
        <>
          Nekazari Core: A FIWARE-Ready AgTech Twin. Licensed under AGPL by {link}.{operatedBy}
        </>
      );
      break;
  }

  return <span className={className}>{text}</span>;
};

