import type { Tender } from './api';

const UNTITLED_TENDER = 'Untitled tender';
const NO_SUMMARY = 'No summary available';
const MAX_DESCRIPTION_SUMMARY_LENGTH = 500;

function nonEmptyText(value: string | null | undefined): string | null {
  const trimmedValue = value?.trim();
  return trimmedValue ? trimmedValue : null;
}

function tenderTitle(tender: Tender): string {
  return nonEmptyText(tender.title) ?? UNTITLED_TENDER;
}

function formatClosingDate(value: string | null): string | null {
  const dateText = nonEmptyText(value);
  if (!dateText) return null;

  const dateOnlyMatch = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateText);
  let parsedDate: Date;

  if (dateOnlyMatch) {
    const [, yearText, monthText, dayText] = dateOnlyMatch;
    const year = Number(yearText);
    const month = Number(monthText);
    const day = Number(dayText);
    parsedDate = new Date(year, month - 1, day);

    if (
      parsedDate.getFullYear() !== year
      || parsedDate.getMonth() !== month - 1
      || parsedDate.getDate() !== day
    ) {
      return null;
    }
  } else {
    parsedDate = new Date(dateText);
    if (Number.isNaN(parsedDate.getTime())) return null;
  }

  return parsedDate.toLocaleDateString('en-AU', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

function originalTenderUrl(value: string | null): string | null {
  const urlText = nonEmptyText(value);
  if (!urlText) return null;

  try {
    const parsedUrl = new URL(urlText);
    if (parsedUrl.protocol !== 'http:' && parsedUrl.protocol !== 'https:') return null;

    const hostname = parsedUrl.hostname.toLowerCase();
    if (
      hostname === 'storage.googleapis.com'
      || hostname.endsWith('.storage.googleapis.com')
      || hostname === 'firebasestorage.googleapis.com'
    ) {
      return null;
    }

    return parsedUrl.href;
  } catch {
    return null;
  }
}

function shortenDescription(description: string): string {
  const characters = Array.from(description);
  if (characters.length <= MAX_DESCRIPTION_SUMMARY_LENGTH) return description;

  const availableText = characters
    .slice(0, MAX_DESCRIPTION_SUMMARY_LENGTH - 1)
    .join('')
    .trimEnd();
  const lastWhitespaceIndex = availableText.search(/\s+\S*$/);
  const readableCutoff = Math.floor(MAX_DESCRIPTION_SUMMARY_LENGTH * 0.8);
  const shortenedText = lastWhitespaceIndex >= readableCutoff
    ? availableText.slice(0, lastWhitespaceIndex).trimEnd()
    : availableText;

  return `${shortenedText}…`;
}

function tenderSummary(tender: Tender): string {
  const headline = nonEmptyText(tender.summary_headline);
  if (headline) return headline;

  const description = nonEmptyText(tender.description);
  return description ? shortenDescription(description) : NO_SUMMARY;
}

export function generateTenderEmailSubject(tender: Tender): string {
  return `Tender opportunity: ${tenderTitle(tender)}`;
}

export function generateTenderEmailBody(tender: Tender, personalMessage = ''): string {
  const sections: string[] = [];
  const message = nonEmptyText(personalMessage);
  if (message) sections.push(message);

  const tenderDetails = [`Tender: ${tenderTitle(tender)}`];
  const organisation = nonEmptyText(tender.issuing_agency);
  if (organisation) tenderDetails.push(`Organisation: ${organisation}`);

  const closingDate = formatClosingDate(tender.closing_date);
  if (closingDate) tenderDetails.push(`Closing date: ${closingDate}`);

  const reference = nonEmptyText(tender.source_reference_id);
  if (reference) tenderDetails.push(`Reference: ${reference}`);

  sections.push(tenderDetails.join('\n'));
  sections.push(`Summary:\n${tenderSummary(tender)}`);

  const sourceUrl = originalTenderUrl(tender.source_url);
  if (sourceUrl) sections.push(`Original tender:\n${sourceUrl}`);

  return sections.join('\n\n');
}
