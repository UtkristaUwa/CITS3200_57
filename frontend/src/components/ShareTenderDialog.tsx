import { useEffect, useId, useState } from 'react';
import {
  Box,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Button,
  TextField,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import type { Tender } from '../lib/api';
import { generateTenderEmailBody, generateTenderEmailSubject } from '../lib/tenderShare';

interface ShareTenderDialogProps {
  tender: Tender;
  open: boolean;
  onClose: () => void;
}

const MAX_MESSAGE_LENGTH = 500;

function validateEmail(value: string): string | null {
  if (!value) return 'Recipient email is required.';
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
    return 'Enter a valid email address.';
  }
  return null;
}

export function ShareTenderDialog({ tender, open, onClose }: ShareTenderDialogProps) {
  const dialogTitleId = useId();
  const emailPreviewHeadingId = useId();
  const [recipientEmail, setRecipientEmail] = useState('');
  const [personalMessage, setPersonalMessage] = useState('');
  const [emailError, setEmailError] = useState<string | null>(null);
  const emailSubject = generateTenderEmailSubject(tender);
  const emailBody = generateTenderEmailBody(tender, personalMessage);

  useEffect(() => {
    if (!open) {
      setRecipientEmail('');
      setPersonalMessage('');
      setEmailError(null);
    }
  }, [open]);

  const resetInputs = () => {
    setRecipientEmail('');
    setPersonalMessage('');
    setEmailError(null);
  };

  const handleClose = () => {
    resetInputs();
    onClose();
  };

  const handleEmailBlur = () => {
    const trimmedEmail = recipientEmail.trim();
    setRecipientEmail(trimmedEmail);
    setEmailError(validateEmail(trimmedEmail));
  };

  const handleEmailChange = (value: string) => {
    setRecipientEmail(value);
    if (emailError) {
      setEmailError(validateEmail(value.trim()));
    }
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      fullWidth
      maxWidth="sm"
      aria-labelledby={dialogTitleId}
      slotProps={{
        paper: {
          sx: {
            m: { xs: 2, sm: 4 },
            width: { xs: 'calc(100% - 32px)', sm: '100%' },
            maxHeight: { xs: 'calc(100% - 32px)', sm: 'calc(100% - 64px)' },
          },
        },
      }}
    >
      <DialogTitle id={dialogTitleId} sx={{ pr: 7 }}>
        Share Tender
        <IconButton
          aria-label="Close share tender dialog"
          onClick={handleClose}
          sx={{
            position: 'absolute',
            right: 8,
            top: 8,
            minWidth: 44,
            minHeight: 44,
          }}
        >
          <CloseIcon />
        </IconButton>
      </DialogTitle>

      <DialogContent dividers sx={{ overflowX: 'hidden' }}>
        <Box sx={{ mb: 3, minWidth: 0 }}>
          <Typography variant="overline" color="text.secondary">
            Tender
          </Typography>
          <Typography
            variant="subtitle1"
            sx={{ fontWeight: 700, overflowWrap: 'anywhere', wordBreak: 'break-word' }}
          >
            {tender.title || 'Untitled tender'}
          </Typography>
        </Box>

        <TextField
          label="Recipient email"
          type="email"
          required
          fullWidth
          autoComplete="email"
          value={recipientEmail}
          onChange={(event) => handleEmailChange(event.target.value)}
          onBlur={handleEmailBlur}
          error={Boolean(emailError)}
          helperText={emailError ?? 'Enter the email address of the person you want to share this tender with.'}
          sx={{ mb: 3 }}
        />

        <TextField
          label="Personal message (optional)"
          multiline
          minRows={4}
          maxRows={8}
          fullWidth
          value={personalMessage}
          onChange={(event) => setPersonalMessage(event.target.value)}
          helperText={`${personalMessage.length}/${MAX_MESSAGE_LENGTH} characters`}
          slotProps={{
            htmlInput: { maxLength: MAX_MESSAGE_LENGTH },
            formHelperText: { sx: { textAlign: 'right' } },
          }}
          sx={{ mb: 3 }}
        />

        <Box
          component="section"
          aria-labelledby={emailPreviewHeadingId}
          sx={{
            p: { xs: 1.5, sm: 2 },
            minWidth: 0,
            border: '1px solid',
            borderColor: 'divider',
            borderRadius: 1,
            bgcolor: 'background.default',
          }}
        >
          <Typography id={emailPreviewHeadingId} variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
            Email preview
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontWeight: 700 }}>
            Subject
          </Typography>
          <Typography
            variant="body2"
            sx={{ overflowWrap: 'anywhere', wordBreak: 'break-word', mb: 2 }}
          >
            {emailSubject}
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontWeight: 700 }}>
            Body
          </Typography>
          <Typography
            variant="body2"
            component="div"
            sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', wordBreak: 'break-word' }}
          >
            {emailBody}
          </Typography>
        </Box>
      </DialogContent>

      <DialogActions sx={{ px: { xs: 2, sm: 3 }, py: 2 }}>
        <Button onClick={handleClose}>Cancel</Button>
      </DialogActions>
    </Dialog>
  );
}
