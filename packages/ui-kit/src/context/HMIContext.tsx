/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React, { createContext, useContext } from 'react';

interface HMIContextType {
  isHmiMode: boolean;
}

const HMIContext = createContext<HMIContextType>({ isHmiMode: false });

export const HMIProvider: React.FC<{ isHmiMode?: boolean; children: React.ReactNode }> = ({ 
  isHmiMode = false, 
  children 
}) => {
  return (
    <HMIContext.Provider value={{ isHmiMode }}>
      <div className={isHmiMode ? 'nkz-hmi-root bg-black text-white' : ''}>
        {children}
      </div>
    </HMIContext.Provider>
  );
};

export const useHMI = () => useContext(HMIContext);
