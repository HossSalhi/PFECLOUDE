package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.core.Alert;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AlertRepository extends JpaRepository<Alert, Long> {}
