import { useState, useEffect } from 'react';
import {
  Box,
  TextField,
  InputAdornment,
  Button,
  Collapse,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Tooltip,
  Alert,
  Checkbox,
  Chip,
  ListItemText,
  OutlinedInput,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import FilterListIcon from '@mui/icons-material/FilterList';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import {
  getSources,
  getFocusAreas,
  DEFAULT_FOCUS_AREAS,
  type TenderSource,
  type TenderTag,
} from '../lib/api';
import { SORT_OPTIONS } from '../lib/tenderSort';
import type { DefaultTenderStatus } from '../lib/tenderPreferences';
import {
  DESKTOP_TOP_NAV_HEIGHT_PX,
  MOBILE_SEARCH_TOP_NAV_HEIGHT_PX,
  MOBILE_TENDER_CARD_CLEARANCE_PX,
} from './TopNav';

export interface TenderFilterBarProps {
  searchQuery: string;
  setSearchQuery: (val: string) => void;
  advancedSearch: boolean;
  setAdvancedSearch: (val: boolean) => void;
  showFilters: boolean;
  setShowFilters: (val: boolean) => void;
  sortBy: string;
  setSortBy: (val: string) => void;
  states: string[];
  setStates: (val: string[]) => void;
  focusAreas: string[];
  setFocusAreas: (val: string[]) => void;
  sourceId: string;
  setSourceId: (val: string) => void;
  year: string;
  setYear: (val: string) => void;
  status: DefaultTenderStatus;
  setStatus: (val: DefaultTenderStatus) => void;
  minDate: string;
  setMinDate: (val: string) => void;
  maxDate: string;
  setMaxDate: (val: string) => void;
  publishedAfter: string;
  setPublishedAfter: (val: string) => void;
  publishedBefore: string;
  setPublishedBefore: (val: string) => void;
  filtersReady: boolean;
  defaultFiltersSaving: boolean;
  defaultFiltersLoadError: string | null;
  defaultFiltersSaveError: string | null;
  defaultFiltersSaved: boolean;
  handleSaveDefaultFilters: () => Promise<void>;
  handleResetFilters: () => void;
}

export function TenderSearchControls(props: TenderFilterBarProps) {
  const isSearchActive = Boolean(props.searchQuery.trim());

  return (
    <Box
      sx={{
        display: 'flex',
        gap: { xs: 0.75, sm: 1 },
        alignItems: 'center',
        minWidth: 0,
        width: '100%',
        '& .MuiInputBase-root': { minHeight: 40 },
      }}
    >
      <TextField
        fullWidth
        variant="outlined"
        size="small"
        placeholder="Search tenders..."
        value={props.searchQuery}
        onChange={(e) => props.setSearchQuery(e.target.value)}
        sx={{ minWidth: { xs: 100, sm: 160 } }}
        slotProps={{
          input: {
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon color="primary" />
              </InputAdornment>
            ),
          },
          htmlInput: { 'aria-label': 'Search tenders' },
        }}
      />
      <Tooltip title="Toggle AI Semantic Search. When active, matches meaning and concepts (slower). When off, uses fast keyword search.">
        <Button
          aria-label="Toggle AI Semantic Search"
          aria-pressed={props.advancedSearch}
          variant={props.advancedSearch ? 'contained' : 'outlined'}
          startIcon={<AutoAwesomeIcon />}
          onClick={() => props.setAdvancedSearch(!props.advancedSearch)}
          sx={{
            flexShrink: 0,
            minWidth: { xs: 40, lg: 'auto' },
            width: { xs: 40, lg: 'auto' },
            height: 40,
            px: { xs: 0, lg: 2 },
            textTransform: 'none',
            fontWeight: props.advancedSearch ? 600 : 500,
            '& .MuiButton-startIcon': {
              m: { xs: 0, lg: '0 8px 0 -4px' },
            },
            ...(props.advancedSearch
              ? {
                  background: 'linear-gradient(135deg, #2D3AF1 0%, #6E78F7 100%)',
                  color: '#fff',
                  borderColor: 'transparent',
                  boxShadow: '0 2px 8px rgba(45, 58, 241, 0.35)',
                  '&:hover': {
                    background: 'linear-gradient(135deg, #232EC2 0%, #5C67ED 100%)',
                  },
                }
              : {
                  color: 'text.secondary',
                  borderColor: 'divider',
                  '&:hover': {
                    borderColor: 'primary.main',
                    color: 'primary.main',
                  },
                }),
          }}
        >
          <Box component="span" sx={{ display: { xs: 'none', lg: 'inline' } }}>
            Advanced
          </Box>
        </Button>
      </Tooltip>

      <FormControl size="small" sx={{ minWidth: { xs: 110, sm: 145, md: 175 }, flexShrink: 0 }}>
        <InputLabel id="tender-sort-label">Sort By</InputLabel>
        <Select
          labelId="tender-sort-label"
          value={props.sortBy}
          label="Sort By"
          onChange={(e) => props.setSortBy(e.target.value)}
          sx={{ height: 40 }}
        >
          {SORT_OPTIONS.map((opt) => {
            if (opt.value === 'relevance' && !isSearchActive) return null;
            return (
              <MenuItem key={opt.value} value={opt.value}>
                {opt.label}
              </MenuItem>
            );
          })}
        </Select>
      </FormControl>

      <Tooltip title={props.showFilters ? 'Hide filters' : 'Show filters'}>
        <Button
          aria-label={props.showFilters ? 'Hide tender filters' : 'Show tender filters'}
          aria-expanded={props.showFilters}
          aria-controls="tender-filter-panel"
          variant={props.showFilters ? 'contained' : 'outlined'}
          startIcon={<FilterListIcon />}
          onClick={() => props.setShowFilters(!props.showFilters)}
          sx={{
            flexShrink: 0,
            minWidth: { xs: 40, lg: 'auto' },
            width: { xs: 40, lg: 'auto' },
            height: 40,
            px: { xs: 0, lg: 2 },
            '& .MuiButton-startIcon': {
              m: { xs: 0, lg: '0 8px 0 -4px' },
            },
          }}
        >
          <Box component="span" sx={{ display: { xs: 'none', lg: 'inline' } }}>
            Filters
          </Box>
        </Button>
      </Tooltip>
    </Box>
  );
}

const SELECTABLE_STATES = ['ACT', 'NSW', 'NT', 'QLD', 'SA', 'TAS', 'VIC', 'WA'] as const;

export function TenderFilterPanel(props: TenderFilterBarProps) {
  const [availableSources, setAvailableSources] = useState<TenderSource[]>([]);
  const [availableFocusAreas, setAvailableFocusAreas] = useState<TenderTag[]>(DEFAULT_FOCUS_AREAS);
  const standardYears = ['2026', '2025', '2024'];
  const yearOptions = props.year && !standardYears.includes(props.year)
    ? [props.year, ...standardYears]
    : standardYears;

  useEffect(() => {
    getSources().then(setAvailableSources);
    getFocusAreas().then(setAvailableFocusAreas);
  }, []);

  return (
    <Collapse id="tender-filter-panel" in={props.showFilters} sx={{ width: '100%' }}>
      <Box
        sx={{
          boxSizing: 'border-box',
          width: '100%',
          maxHeight: {
            xs: `calc(100dvh - ${MOBILE_SEARCH_TOP_NAV_HEIGHT_PX + MOBILE_TENDER_CARD_CLEARANCE_PX}px)`,
            sm: `calc(100dvh - ${MOBILE_SEARCH_TOP_NAV_HEIGHT_PX}px)`,
            md: `calc(100dvh - ${DESKTOP_TOP_NAV_HEIGHT_PX}px)`,
          },
          overflowY: 'auto',
          overscrollBehaviorY: 'contain',
          WebkitOverflowScrolling: 'touch',
          px: { xs: 1, sm: 2 },
          pb: 1,
        }}
      >
        <Box
          role="region"
          aria-label="Tender filters"
          sx={{
            display: 'flex',
            flexDirection: 'column',
            gap: 1.5,
            width: '100%',
            maxWidth: (theme) => theme.breakpoints.values.md,
            mx: 'auto',
            p: { xs: 1.5, sm: 2 },
            bgcolor: 'background.paper',
            border: '1px solid',
            borderColor: 'secondary.main',
            borderRadius: 2,
            boxShadow: (theme) => theme.palette.mode === 'light'
              ? '0 3px 10px rgba(36,45,50,0.08)'
              : '0 3px 10px rgba(0,0,0,0.24)',
            '& .MuiInputBase-root': { minHeight: { xs: 44, sm: 40 } },
          }}
        >
          {/* Row 1: States, Source Portal, Status */}
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: 'repeat(2, minmax(0, 1fr))', md: 'repeat(3, minmax(0, 1fr))' },
              gap: 1.5,
            }}
          >
            <FormControl size="small" fullWidth sx={{ minWidth: 0 }}>
              {/* displayEmpty keeps "All" visible with nothing selected, but
                  MUI only shrinks the label when there IS a value -- without
                  shrink/notched the label renders on top of "All". */}
              <InputLabel id="tender-states-label" shrink>States</InputLabel>
              <Select
                multiple
                labelId="tender-states-label"
                value={props.states}
                input={<OutlinedInput label="States" notched />}
                onChange={(event) => {
                  const value = event.target.value;
                  props.setStates(typeof value === 'string' ? value.split(',') : value);
                }}
                renderValue={(selected) => (
                  selected.length === 0
                    ? 'All'
                    : (
                      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                        {selected.map((code) => <Chip key={code} label={code} size="small" />)}
                      </Box>
                    )
                )}
                displayEmpty
              >
                {SELECTABLE_STATES.map((code) => (
                  <MenuItem key={code} value={code}>
                    <Checkbox checked={props.states.includes(code)} size="small" />
                    <ListItemText primary={code} />
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            <FormControl size="small" fullWidth sx={{ minWidth: 0 }}>
              <InputLabel id="tender-source-label">Source Portal</InputLabel>
              <Select
                labelId="tender-source-label"
                value={props.sourceId}
                label="Source Portal"
                onChange={(e) => props.setSourceId(e.target.value)}
              >
                <MenuItem value=""><em>All Portals</em></MenuItem>
                {availableSources.map((src) => (
                  <MenuItem key={src.source_id} value={src.source_id}>
                    {src.name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            <FormControl size="small" fullWidth sx={{ minWidth: 0 }}>
              <InputLabel>Status</InputLabel>
              <Select
                value={props.status}
                label="Status"
                onChange={(e) => props.setStatus(e.target.value as DefaultTenderStatus)}
              >
                <MenuItem value=""><em>All</em></MenuItem>
                <MenuItem value="open">Open</MenuItem>
                <MenuItem value="closed">Closed</MenuItem>
                <MenuItem value="awarded">Awarded</MenuItem>
                <MenuItem value="unknown">Unknown</MenuItem>
              </Select>
            </FormControl>
          </Box>

          {/* Row 2: Focus Areas, Year */}
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: 'minmax(0, 2fr) minmax(0, 1fr)' },
              gap: 1.5,
            }}
          >
            <FormControl size="small" fullWidth sx={{ minWidth: 0 }}>
              <InputLabel id="tender-focus-areas-label" shrink>Focus Areas</InputLabel>
              <Select
                multiple
                labelId="tender-focus-areas-label"
                value={props.focusAreas}
                input={<OutlinedInput label="Focus Areas" notched />}
                onChange={(event) => {
                  const value = event.target.value;
                  props.setFocusAreas(typeof value === 'string' ? value.split(',') : value);
                }}
                renderValue={(selected) => (
                  selected.length === 0
                    ? 'All'
                    : (
                      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                        {selected.map((tagId) => {
                          const item = availableFocusAreas.find((fa) => fa.id === tagId);
                          return <Chip key={tagId} label={item ? item.label : tagId} size="small" />;
                        })}
                      </Box>
                    )
                )}
                displayEmpty
              >
                {availableFocusAreas.map((fa) => (
                  <MenuItem key={fa.id} value={fa.id}>
                    <Checkbox checked={props.focusAreas.includes(fa.id)} size="small" />
                    <ListItemText primary={fa.label} />
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            <FormControl size="small" fullWidth sx={{ minWidth: 0 }}>
              <InputLabel>Year</InputLabel>
              <Select value={props.year} label="Year" onChange={(e) => props.setYear(e.target.value)}>
                <MenuItem value=""><em>All Time</em></MenuItem>
                {yearOptions.map((year) => (
                  <MenuItem key={year} value={year}>{year}</MenuItem>
                ))}
              </Select>
            </FormControl>
          </Box>

          {/* Row 3: Closing Date Range */}
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: 'repeat(2, minmax(0, 1fr))' },
              gap: 1.5,
            }}
          >
            <TextField
              size="small"
              fullWidth
              type="date"
              label="Closing After"
              slotProps={{ inputLabel: { shrink: true } }}
              value={props.minDate}
              onChange={(e) => props.setMinDate(e.target.value)}
              sx={{ minWidth: 0 }}
            />
            <TextField
              size="small"
              fullWidth
              type="date"
              label="Closing Before"
              slotProps={{ inputLabel: { shrink: true } }}
              value={props.maxDate}
              onChange={(e) => props.setMaxDate(e.target.value)}
              sx={{ minWidth: 0 }}
            />
          </Box>

          {/* Row 4: Published Date Range */}
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: 'repeat(2, minmax(0, 1fr))' },
              gap: 1.5,
            }}
          >
            <TextField
              size="small"
              fullWidth
              type="date"
              label="Published After"
              slotProps={{ inputLabel: { shrink: true } }}
              value={props.publishedAfter}
              onChange={(e) => props.setPublishedAfter(e.target.value)}
              sx={{ minWidth: 0 }}
            />
            <TextField
              size="small"
              fullWidth
              type="date"
              label="Published Before"
              slotProps={{ inputLabel: { shrink: true } }}
              value={props.publishedBefore}
              onChange={(e) => props.setPublishedBefore(e.target.value)}
              sx={{ minWidth: 0 }}
            />
          </Box>

          {props.defaultFiltersLoadError && (
            <Alert severity="warning">
              {props.defaultFiltersLoadError}
            </Alert>
          )}
          {props.defaultFiltersSaveError && (
            <Alert severity="error">
              {props.defaultFiltersSaveError}
            </Alert>
          )}
          {props.defaultFiltersSaved && (
            <Alert severity="success">
              Current filters saved as your default.
            </Alert>
          )}

          <Box
            sx={{
              display: 'flex',
              flexDirection: { xs: 'column', sm: 'row' },
              justifyContent: 'space-between',
              alignItems: { xs: 'stretch', sm: 'center' },
              gap: 1,
              mt: 1,
            }}
          >
            <Button
              size="small"
              variant="contained"
              onClick={() => void props.handleSaveDefaultFilters()}
              disabled={!props.filtersReady || props.defaultFiltersSaving}
              sx={{ minHeight: { xs: 44, sm: 30 } }}
            >
              {props.defaultFiltersSaving ? 'Saving...' : 'Save as Default'}
            </Button>
            <Button size="small" color="primary" onClick={props.handleResetFilters} sx={{ minHeight: { xs: 44, sm: 30 } }}>
              Clear All Filters
            </Button>
          </Box>
        </Box>
      </Box>
    </Collapse>
  );
}
