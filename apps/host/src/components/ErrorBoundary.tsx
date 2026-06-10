import React, { ErrorInfo, ReactNode } from 'react';
import { ServerError } from './error/ServerError';
import { logger } from '@/utils/logger';

interface Props {
    children: ReactNode;
    fallback?: ReactNode | ((error: Error | null) => ReactNode);
    componentName?: string;
}

interface State {
    hasError: boolean;
    error: Error | null;
}

export class ErrorBoundary extends React.Component<Props, State> {
    public state: State = {
        hasError: false,
        error: null,
    };

    constructor(props: Props) {
        super(props);
        if (import.meta.env.DEV) {
            logger.debug(`[ErrorBoundary] Constructor called for: ${props.componentName || 'unknown'}`);
        }
    }

    public static getDerivedStateFromError(error: Error): State {
        logger.error('[ErrorBoundary] getDerivedStateFromError CAUGHT:', error?.message, error?.stack);
        return { hasError: true, error };
    }

    public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        logger.error(`[ErrorBoundary] componentDidCatch in ${this.props.componentName || 'component'}:`, error, errorInfo);
        logger.error('[ErrorBoundary] Error message:', error?.message);
        logger.error('[ErrorBoundary] Error stack:', error?.stack);
        logger.error('[ErrorBoundary] Component stack:', errorInfo?.componentStack);
    }

    public render() {
        if (import.meta.env.DEV) {
            logger.debug(`[ErrorBoundary] Render called for: ${this.props.componentName}, hasError: ${this.state.hasError}`);
        }
        if (this.state.hasError) {
            logger.error('[ErrorBoundary] Rendering fallback, error was:', this.state.error?.message);
            if (this.props.fallback) {
                // Support both ReactNode and render function
                if (typeof this.props.fallback === 'function') {
                    return this.props.fallback(this.state.error);
                }
                return this.props.fallback;
            }

            // Use ServerError component for better UX
            return (
                <ServerError 
                    error={this.state.error ?? undefined} 
                    resetError={() => {
                        this.setState({ hasError: false, error: null });
                    }}
                />
            );
        }

        return this.props.children;
    }
}
